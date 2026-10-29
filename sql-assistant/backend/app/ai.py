"""
AI Provider abstraction and Google Gemini implementation using google-genai SDK.

Keeps AI logic swappable and decoupled from FastAPI route handlers.
Handles rate-limits (HTTP 429) with exponential/delayed backoff retry.
"""

from abc import ABC, abstractmethod
import asyncio
import os
import time
from typing import AsyncGenerator, Optional

from dotenv import load_dotenv

# Ensure .env is loaded from workspace root
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

    async def generate_with_tools(self, prompt: str, tools: list) -> dict:
        """Tool/function calling hook for future extensions."""
        raise NotImplementedError("Tool calling not implemented for this provider yet.")


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
        # Initialize client with explicitly provided api_key
        self.client = genai.Client(api_key=self.api_key)

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

        # Retry once on rate-limit (429) or transient server errors
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                # generate_content_stream in google-genai is a synchronous generator
                response_stream = self.client.models.generate_content_stream(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )

                for chunk in response_stream:
                    if chunk.text:
                        yield chunk.text
                        # Yield control to event loop for smooth SSE delivery
                        await asyncio.sleep(0)
                return

            except APIError as exc:
                is_rate_limit = exc.code == 429 or "RESOURCE_EXHAUSTED" in str(exc)
                if is_rate_limit and attempt < max_attempts:
                    # Delay and retry once
                    await asyncio.sleep(3.0)
                    continue

                if is_rate_limit:
                    raise AIProviderError(
                        "Gemini API rate limit reached (HTTP 429). Please wait a moment and try again.",
                        status_code=429,
                    )
                raise AIProviderError(
                    f"Gemini API error ({exc.code}): {exc.message}",
                    status_code=exc.code or 500,
                )
            except Exception as exc:
                if attempt < max_attempts and "429" in str(exc):
                    await asyncio.sleep(3.0)
                    continue
                raise AIProviderError(f"Unexpected AI error: {str(exc)}", status_code=500)


def get_ai_provider() -> BaseAIProvider:
    """Factory to get the configured AI provider."""
    provider_name = os.getenv("AI_PROVIDER", "gemini").lower()
    if provider_name == "gemini":
        return GeminiProvider()
    raise AIProviderError(f"Unsupported AI_PROVIDER: {provider_name}. Expected 'gemini'.")
