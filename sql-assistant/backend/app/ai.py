"""
AI Provider abstraction and Google Gemini implementation using google-genai SDK.

Features:
- Abstract BaseAIProvider interface.
- GeminiProvider supporting multi-turn chat and tool calling (function declarations).
- Rate-limit (HTTP 429) delayed backoff and retry.
- Tool use loop capped at 4 rounds with fallback to tool-free answering on repeated errors.
"""

from abc import ABC, abstractmethod
import asyncio
import os
import re
from typing import AsyncGenerator, Optional, Callable, Any

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


class AIProviderError(Exception):
    """Custom exception raised for AI service errors."""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BaseAIProvider(ABC):
    """Abstract interface for AI chat and generation providers."""

    @abstractmethod
    async def stream_chat(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
    ) -> AsyncGenerator[str, None]:
        """Stream chunks of text for a prompt."""
        pass

    @abstractmethod
    async def stream_chat_with_tools(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        tools: Optional[list] = None,
        tool_executor: Optional[Callable[[str, dict], Any]] = None,
        temperature: float = 0.2,
        history: Optional[list[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Stream events (tool activity and text chunks) with multi-turn tool calling.
        Yields dicts of:
        - {"type": "tool_call", "message": "Looking up orders schema..."}
        - {"type": "text", "chunk": "..."}
        """
        pass

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        """Generate complete text response (for validation retries)."""
        pass


def get_sql_tool_declarations(allow_sampling: bool = False):
    """Define Gemini function declarations for SQL Assistant tools."""
    from google.genai import types

    func_decls = [
        types.FunctionDeclaration(
            name="get_table_schema",
            description="Get the list of columns and data types for a database table.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "table_name": types.Schema(type=types.Type.STRING, description="Name of the table")
                },
                required=["table_name"],
            ),
        ),
        types.FunctionDeclaration(
            name="run_explain",
            description="Run PostgreSQL EXPLAIN on a SQL query to inspect its execution plan.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "sql": types.Schema(type=types.Type.STRING, description="The SQL query to explain")
                },
                required=["sql"],
            ),
        ),
    ]

    if allow_sampling:
        func_decls.append(
            types.FunctionDeclaration(
                name="sample_values",
                description="Sample up to 3 non-null values from a table column (opt-in only).",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "table_name": types.Schema(type=types.Type.STRING, description="Name of the table"),
                        "column_name": types.Schema(type=types.Type.STRING, description="Name of the column"),
                    },
                    required=["table_name", "column_name"],
                ),
            )
        )

    return [types.Tool(function_declarations=func_decls)]


class GeminiProvider(BaseAIProvider):
    """Google Gemini provider implementation using google-genai SDK."""

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model = os.getenv("GEMINI_MODEL", "").strip()

        if not self.api_key:
            raise AIProviderError(
                "GEMINI_API_KEY is missing. Please set GEMINI_API_KEY in your .env file.",
                status_code=500,
            )
        if not self.model:
            raise AIProviderError(
                "GEMINI_MODEL is missing. Please set GEMINI_MODEL (e.g. gemini-2.5-flash) in your .env file.",
                status_code=500,
            )

        from google import genai
        self.client = genai.Client(api_key=self.api_key)

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        from google.genai import types
        from google.genai.errors import APIError

        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_instruction if system_instruction else None,
        )

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
                return resp.text or ""
            except APIError as exc:
                if (exc.code == 429 or "RESOURCE_EXHAUSTED" in str(exc)) and attempt < max_attempts:
                    await asyncio.sleep(3.0)
                    continue
                raise AIProviderError(f"Gemini error: {exc.message}", status_code=exc.code or 500)
            except Exception as exc:
                if attempt < max_attempts and "429" in str(exc):
                    await asyncio.sleep(3.0)
                    continue
                raise AIProviderError(f"Unexpected AI error: {str(exc)}", status_code=500)
        return ""

    async def stream_chat(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
    ) -> AsyncGenerator[str, None]:
        from google.genai import types
        from google.genai.errors import APIError

        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_instruction if system_instruction else None,
        )

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                response_stream = self.client.models.generate_content_stream(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )

                for chunk in response_stream:
                    if chunk.text:
                        yield chunk.text
                        await asyncio.sleep(0)
                return
            except APIError as exc:
                if (exc.code == 429 or "RESOURCE_EXHAUSTED" in str(exc)) and attempt < max_attempts:
                    await asyncio.sleep(3.0)
                    continue
                raise AIProviderError(f"Gemini error: {exc.message}", status_code=exc.code or 500)
            except Exception as exc:
                if attempt < max_attempts and "429" in str(exc):
                    await asyncio.sleep(3.0)
                    continue
                raise AIProviderError(f"Unexpected AI error: {str(exc)}", status_code=500)

    async def stream_chat_with_tools(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        tools: Optional[list] = None,
        tool_executor: Optional[Callable[[str, dict], Any]] = None,
        temperature: float = 0.2,
        history: Optional[list[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        from google.genai import types
        from google.genai.errors import APIError

        # Build initial conversation contents
        contents: list[types.Content] = []
        if history:
            for msg in history:
                role = "model" if msg.get("role") in ("assistant", "model") else "user"
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=msg.get("content", ""))]
                    )
                )

        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)]
            )
        )

        max_rounds = 4
        error_count = 0

        for round_idx in range(max_rounds):
            # If repeated tool errors or last round, disable tools to guarantee an answer
            use_tools = (tools is not None) and (tool_executor is not None) and (error_count < 2) and (round_idx < max_rounds - 1)

            config = types.GenerateContentConfig(
                temperature=temperature,
                system_instruction=system_instruction if system_instruction else None,
                tools=tools if use_tools else None,
            )

            # If tools are not active, stream directly to end user
            if not use_tools:
                try:
                    stream = self.client.models.generate_content_stream(
                        model=self.model,
                        contents=contents,
                        config=config,
                    )
                    for chunk in stream:
                        if chunk.text:
                            yield {"type": "text", "chunk": chunk.text}
                            await asyncio.sleep(0)
                    return
                except APIError as exc:
                    raise AIProviderError(f"Gemini error: {exc.message}", status_code=exc.code or 500)
                except Exception as exc:
                    raise AIProviderError(f"Unexpected AI error: {str(exc)}", status_code=500)

            # With tools enabled, call generate_content to check for function calls
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except APIError as exc:
                if exc.code == 429 or "RESOURCE_EXHAUSTED" in str(exc):
                    await asyncio.sleep(3.0)
                    response = self.client.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=config,
                    )
                else:
                    raise AIProviderError(f"Gemini error: {exc.message}", status_code=exc.code or 500)

            # If no function calls, the model gave a direct response
            if not response.function_calls:
                text = response.text or ""
                if text:
                    yield {"type": "text", "chunk": text}
                return

            # Append model turn to conversation history
            if response.candidates and response.candidates[0].content:
                contents.append(response.candidates[0].content)

            # Process each requested function call
            response_parts = []
            for fn_call in response.function_calls:
                fn_name = fn_call.name
                fn_args = dict(fn_call.args) if fn_call.args else {}

                # Status line description
                if fn_name == "get_table_schema":
                    tbl = fn_args.get("table_name", "unknown")
                    status_line = f"Looking up schema for table '{tbl}'..."
                elif fn_name == "run_explain":
                    status_line = "Running PostgreSQL EXPLAIN plan..."
                elif fn_name == "sample_values":
                    tbl = fn_args.get("table_name", "")
                    col = fn_args.get("column_name", "")
                    status_line = f"Sampling values for {tbl}.{col}..."
                else:
                    status_line = f"Executing tool {fn_name}..."

                yield {"type": "tool_call", "message": status_line}
                await asyncio.sleep(0)

                # Execute tool
                try:
                    result = tool_executor(fn_name, fn_args)
                    response_parts.append(
                        types.Part.from_function_response(
                            name=fn_name,
                            response={"result": result}
                        )
                    )
                except Exception as e:
                    error_count += 1
                    response_parts.append(
                        types.Part.from_function_response(
                            name=fn_name,
                            response={"error": f"Error executing {fn_name}: {str(e)}"}
                        )
                    )

            # Append function responses as user turn
            contents.append(
                types.Content(
                    role="user",
                    parts=response_parts
                )
            )

        # Fallback if loop finishes: stream answer without tools
        config_fallback = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_instruction if system_instruction else None,
            tools=None,
        )
        stream = self.client.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=config_fallback,
        )
        for chunk in stream:
            if chunk.text:
                yield {"type": "text", "chunk": chunk.text}
                await asyncio.sleep(0)


def get_ai_provider() -> BaseAIProvider:
    """Factory to get the configured AI provider."""
    provider_name = os.getenv("AI_PROVIDER", "gemini").lower()
    if provider_name == "gemini":
        return GeminiProvider()
    raise AIProviderError(f"Unsupported AI_PROVIDER: {provider_name}. Expected 'gemini'.")
