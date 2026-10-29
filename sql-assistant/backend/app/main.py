import json
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


class FixRequest(BaseModel):
    sql: str
    error_message: str
    error_position: Optional[int] = None
    error_hint: Optional[str] = None


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
    """Stream plain-language explanation of SQL query using SSE."""
    if not req.sql.strip():
        return JSONResponse(status_code=400, content={"error": {"message": "Query is empty"}})

    schema_text = _format_schema_for_ai(req.sql)
    prompt = prompts.EXPLAIN_PROMPT_TEMPLATE.format(
        sql=req.sql,
        schema_text=schema_text,
    )

    async def event_generator():
        try:
            provider = ai.get_ai_provider()
            async for token in provider.stream_chat(
                prompt=prompt,
                system_instruction=prompts.SYSTEM_PROMPT,
                temperature=0.2,
            ):
                payload = json.dumps({"text": token})
                yield f"data: {payload}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except ai.AIProviderError as e:
            payload = json.dumps({"error": e.message})
            yield f"data: {payload}\n\n"
        except Exception as e:
            payload = json.dumps({"error": f"AI error: {str(e)}"})
            yield f"data: {payload}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/ai/fix")
async def ai_fix(req: FixRequest):
    """Stream cause of error + corrected SQL query using SSE."""
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

    async def event_generator():
        try:
            provider = ai.get_ai_provider()
            async for token in provider.stream_chat(
                prompt=prompt,
                system_instruction=prompts.SYSTEM_PROMPT,
                temperature=0.2,
            ):
                payload = json.dumps({"text": token})
                yield f"data: {payload}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except ai.AIProviderError as e:
            payload = json.dumps({"error": e.message})
            yield f"data: {payload}\n\n"
        except Exception as e:
            payload = json.dumps({"error": f"AI error: {str(e)}"})
            yield f"data: {payload}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
