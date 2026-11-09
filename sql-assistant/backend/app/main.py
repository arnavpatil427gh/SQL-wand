import json
import re
from typing import Optional
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import db
from . import sql_tools
from . import ai
from . import prompts

app = FastAPI(title="SQL Assistant API")


class QueryRequest(BaseModel):
    sql: str
    allow_sampling: bool = False


class FixRequest(BaseModel):
    sql: str
    error_message: str
    error_position: Optional[int] = None
    error_hint: Optional[str] = None
    allow_sampling: bool = False


class OptimizeRequest(BaseModel):
    sql: str
    allow_sampling: bool = False


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    sql: str
    messages: list[ChatMessage]
    allow_sampling: bool = False


def _format_schema_for_ai(sql: str) -> str:
    """
    Extract tables referenced in `sql` and format only their schema for the prompt.
    If no tables could be parsed (e.g. malformed syntax), falls back to the full schema.
    """
    all_tables = db.get_schema()
    extracted = sql_tools.extract_tables_and_columns(sql)
    referenced_names = {t["name"].lower() for t in extracted.get("tables", []) if t.get("name")}

    if referenced_names:
        matched = [t for t in all_tables if t["name"].lower() in referenced_names]
    else:
        matched = []

    # Fallback to all tables if none were matched or parsed
    tables_to_format = matched if matched else all_tables

    lines = []
    for table in tables_to_format:
        cols = ", ".join(f"{c['name']} ({c['type']})" for c in table["columns"])
        lines.append(f"Table '{table['name']}': {cols}")
    return "\n".join(lines) if lines else "No schema tables available."


def make_tool_executor(allow_sampling: bool = False):
    """Factory creating the sandboxed tool dispatcher for Gemini function calls."""
    def executor(name: str, args: dict) -> dict:
        if name == "get_table_schema":
            table_name = str(args.get("table_name", "")).strip()
            cols = db.get_single_table_schema(table_name)
            return {"table": table_name, "columns": cols}
        elif name == "run_explain":
            sql_to_explain = str(args.get("sql", "")).strip()
            plan = db.explain_query(sql_to_explain)
            return {"plan": plan}
        elif name == "sample_values":
            if not allow_sampling:
                return {"error": "Column sampling is disabled. User has not opted in."}
            table_name = str(args.get("table_name", "")).strip()
            column_name = str(args.get("column_name", "")).strip()
            samples = db.sample_column_values(table_name, column_name, limit=3)
            return {"table": table_name, "column": column_name, "samples": samples}
        else:
            raise ValueError(f"Unknown tool '{name}'")
    return executor


def extract_sql_from_markdown(text: str) -> Optional[str]:
    """Extract SQL statement inside ```sql ... ``` code fence."""
    match = re.search(r"```(?:sql)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if match:
        clean = match.group(1).strip()
        return clean if clean else None
    return None


def validate_sql_fix(sql_text: str) -> tuple[bool, Optional[str]]:
    """
    Validate a suggested SQL fix:
    1. Parse with sqlglot for syntax errors.
    2. Dry-run with PostgreSQL EXPLAIN.
    Returns (is_valid, error_description).
    """
    clean_sql = sql_text.strip().rstrip(";")
    if not clean_sql:
        return False, "Query is empty."

    # 1. Syntax check via sqlglot
    syntax_errors = sql_tools.validate(clean_sql)
    if syntax_errors:
        err_msg = syntax_errors[0].get("message", "Syntax error")
        line = syntax_errors[0].get("line", 1)
        col = syntax_errors[0].get("column", 1)
        return False, f"Syntax error at line {line}, col {col}: {err_msg}"

    # 2. Database engine dry-run check via EXPLAIN
    try:
        db.explain_query(clean_sql)
        return True, None
    except db.QueryError as e:
        return False, e.message
    except Exception as e:
        return False, str(e)


def trim_history(messages: list[ChatMessage], max_chars: int = 12000) -> list[ChatMessage]:
    """Trim old history to stay within a reasonable token budget (~3000 tokens)."""
    total_chars = sum(len(m.content) for m in messages)
    if total_chars <= max_chars:
        return messages

    trimmed = []
    accumulated = 0
    for m in reversed(messages):
        if accumulated + len(m.content) > max_chars and trimmed:
            break
        trimmed.append(m)
        accumulated += len(m.content)

    trimmed.reverse()
    if trimmed and trimmed[0].role != "user" and len(trimmed) > 1:
        trimmed = trimmed[1:]
    return trimmed


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/schema")
def schema():
    return {"tables": db.get_schema()}


@app.post("/api/query")
def query(req: QueryRequest):
    if not req.sql.strip():
        return JSONResponse(status_code=400, content={"error": {"message": "Query is empty"}})
    try:
        return jsonable_encoder(db.run_query(req.sql))
    except db.QueryError as e:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": e.message, "position": e.position, "hint": e.hint}},
        )


@app.post("/api/analyze")
def analyze(req: QueryRequest):
    """Return syntax validity, error locations, and referenced tables/columns."""
    errs = sql_tools.validate(req.sql)
    info = sql_tools.extract_tables_and_columns(req.sql)
    return {
        "valid": len(errs) == 0,
        "errors": errs,
        "tables": info["tables"],
        "columns": info["columns"],
    }


@app.post("/api/format")
def format_query(req: QueryRequest):
    """Return pretty-printed SQL."""
    return {"sql": sql_tools.format_sql(req.sql)}


@app.post("/api/ai/explain")
async def ai_explain(req: QueryRequest):
    """Stream plain-language explanation of SQL query using SSE and tool support."""
    if not req.sql.strip():
        return JSONResponse(status_code=400, content={"error": {"message": "Query is empty"}})

    schema_text = _format_schema_for_ai(req.sql)
    prompt = prompts.EXPLAIN_PROMPT_TEMPLATE.format(
        sql=req.sql,
        schema_text=schema_text,
    )
    tools = ai.get_sql_tool_declarations(allow_sampling=req.allow_sampling)
    executor = make_tool_executor(allow_sampling=req.allow_sampling)

    async def event_generator():
        try:
            provider = ai.get_ai_provider()
            async for event in provider.stream_chat_with_tools(
                prompt=prompt,
                system_instruction=prompts.SYSTEM_PROMPT,
                tools=tools,
                tool_executor=executor,
                temperature=0.2,
            ):
                if event["type"] == "tool_call":
                    yield f"data: {json.dumps({'tool_call': event['message']})}\n\n"
                elif event["type"] == "text":
                    yield f"data: {json.dumps({'text': event['chunk']})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except ai.AIProviderError as e:
            yield f"data: {json.dumps({'error': e.message})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'AI error: {str(e)}'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/ai/optimize")
async def ai_optimize(req: OptimizeRequest):
    """Stream query optimization suggestions derived from EXPLAIN plan analysis."""
    if not req.sql.strip():
        return JSONResponse(status_code=400, content={"error": {"message": "Query is empty"}})

    # Attempt to obtain execution plan
    try:
        plan = db.explain_query(req.sql)
    except Exception as e:
        plan = f"(Could not run EXPLAIN: {str(e)})"

    schema_text = _format_schema_for_ai(req.sql)
    prompt = prompts.OPTIMIZE_PROMPT_TEMPLATE.format(
        sql=req.sql,
        plan=plan,
        schema_text=schema_text,
    )
    tools = ai.get_sql_tool_declarations(allow_sampling=req.allow_sampling)
    executor = make_tool_executor(allow_sampling=req.allow_sampling)

    async def event_generator():
        try:
            provider = ai.get_ai_provider()
            async for event in provider.stream_chat_with_tools(
                prompt=prompt,
                system_instruction=prompts.SYSTEM_PROMPT,
                tools=tools,
                tool_executor=executor,
                temperature=0.2,
            ):
                if event["type"] == "tool_call":
                    yield f"data: {json.dumps({'tool_call': event['message']})}\n\n"
                elif event["type"] == "text":
                    yield f"data: {json.dumps({'text': event['chunk']})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except ai.AIProviderError as e:
            yield f"data: {json.dumps({'error': e.message})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'AI error: {str(e)}'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/ai/fix")
async def ai_fix(req: FixRequest):
    """
    Diagnose error, generate fix, validate with sqlglot + PostgreSQL EXPLAIN dry-run.
    If validation fails, retries once with the validation error before returning.
    Emits validation status event and streams the explanation and fix.
    """
    if not req.sql.strip():
        return JSONResponse(status_code=400, content={"error": {"message": "Query is empty"}})

    schema_text = _format_schema_for_ai(req.sql)
    prompt = prompts.FIX_PROMPT_TEMPLATE.format(
        sql=req.sql,
        error_message=req.error_message,
        error_position=str(req.error_position) if req.error_position is not None else "N/A",
        error_hint=req.error_hint or "None",
        schema_text=schema_text,
    )
    tools = ai.get_sql_tool_declarations(allow_sampling=req.allow_sampling)
    executor = make_tool_executor(allow_sampling=req.allow_sampling)

    async def event_generator():
        try:
            provider = ai.get_ai_provider()

            # 1. Generate initial response
            tool_events = []
            full_text_chunks = []
            async for event in provider.stream_chat_with_tools(
                prompt=prompt,
                system_instruction=prompts.SYSTEM_PROMPT,
                tools=tools,
                tool_executor=executor,
                temperature=0.2,
            ):
                if event["type"] == "tool_call":
                    tool_events.append(event["message"])
                elif event["type"] == "text":
                    full_text_chunks.append(event["chunk"])

            full_text = "".join(full_text_chunks)
            suggested_sql = extract_sql_from_markdown(full_text)

            is_valid = False
            validation_error = None

            if suggested_sql:
                is_valid, validation_error = validate_sql_fix(suggested_sql)

                # Retry once if validation failed
                if not is_valid:
                    tool_events.append("Fix failed validation. Retrying with database error...")
                    retry_prompt = prompts.FIX_RETRY_PROMPT_TEMPLATE.format(
                        validation_error=validation_error,
                        attempted_sql=suggested_sql,
                        schema_text=schema_text,
                    )
                    retry_text = await provider.generate_text(
                        prompt=retry_prompt,
                        system_instruction=prompts.SYSTEM_PROMPT,
                        temperature=0.2,
                    )
                    retry_sql = extract_sql_from_markdown(retry_text)
                    if retry_sql:
                        retry_valid, retry_err = validate_sql_fix(retry_sql)
                        full_text = retry_text
                        is_valid = retry_valid
                        validation_error = retry_err
                    else:
                        full_text = retry_text

            # Stream out any tool activity lines
            for msg in tool_events:
                yield f"data: {json.dumps({'tool_call': msg})}\n\n"

            # Send validation status badge
            yield f"data: {json.dumps({'validation': {'validated': is_valid, 'error': validation_error}})}\n\n"

            # Stream text in small chunks for smooth typing effect
            chunk_size = 20
            for i in range(0, len(full_text), chunk_size):
                yield f"data: {json.dumps({'text': full_text[i:i + chunk_size]})}\n\n"

            yield f"data: {json.dumps({'done': True})}\n\n"

        except ai.AIProviderError as e:
            yield f"data: {json.dumps({'error': e.message})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'AI error: {str(e)}'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/ai/chat")
async def ai_chat(req: ChatRequest):
    """Follow-up conversational questions about the query with history trimming."""
    if not req.messages:
        return JSONResponse(status_code=400, content={"error": {"message": "No messages provided"}})

    schema_text = _format_schema_for_ai(req.sql)
    system_instruction = prompts.CHAT_SYSTEM_PROMPT.format(
        sql=req.sql,
        schema_text=schema_text,
    )

    # Trim history to fit token budget (~3000 tokens)
    trimmed_msgs = trim_history(req.messages)
    latest_msg = trimmed_msgs[-1].content
    history_dicts = [{"role": m.role, "content": m.content} for m in trimmed_msgs[:-1]]

    tools = ai.get_sql_tool_declarations(allow_sampling=req.allow_sampling)
    executor = make_tool_executor(allow_sampling=req.allow_sampling)

    async def event_generator():
        try:
            provider = ai.get_ai_provider()
            async for event in provider.stream_chat_with_tools(
                prompt=latest_msg,
                system_instruction=system_instruction,
                tools=tools,
                tool_executor=executor,
                temperature=0.2,
                history=history_dicts,
            ):
                if event["type"] == "tool_call":
                    yield f"data: {json.dumps({'tool_call': event['message']})}\n\n"
                elif event["type"] == "text":
                    yield f"data: {json.dumps({'text': event['chunk']})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except ai.AIProviderError as e:
            yield f"data: {json.dumps({'error': e.message})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'AI error: {str(e)}'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
