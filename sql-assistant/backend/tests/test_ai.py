"""
Unit and integration tests for Phase 3 AI Provider and API endpoints.
"""

import pytest
from unittest.mock import patch

from app.prompts import SYSTEM_PROMPT, EXPLAIN_PROMPT_TEMPLATE, FIX_PROMPT_TEMPLATE
from app.ai import AIProviderError, get_ai_provider
from app.main import _format_schema_for_ai


def test_prompts_structure():
    assert "strict" in SYSTEM_PROMPT.lower() or "mentor" in SYSTEM_PROMPT.lower()
    explain_formatted = EXPLAIN_PROMPT_TEMPLATE.format(
        sql="SELECT * FROM customers;",
        schema_text="Table 'customers': id (int), name (text)"
    )
    assert "SELECT * FROM customers;" in explain_formatted
    assert "Table 'customers'" in explain_formatted

    fix_formatted = FIX_PROMPT_TEMPLATE.format(
        sql="SELEC * FROM customers;",
        error_message="syntax error at or near 'SELEC'",
        error_position="1",
        error_hint="None",
        schema_text="Table 'customers': id (int)"
    )
    assert "SELEC * FROM customers;" in fix_formatted
    assert "syntax error" in fix_formatted
    assert "```sql" in fix_formatted


def test_format_schema_filters_relevant_tables():
    mock_tables = [
        {"name": "customers", "columns": [{"name": "id", "type": "int"}, {"name": "name", "type": "text"}]},
        {"name": "orders", "columns": [{"name": "id", "type": "int"}, {"name": "total", "type": "numeric"}]},
    ]
    with patch("app.db.get_schema", return_value=mock_tables):
        schema = _format_schema_for_ai("SELECT name FROM customers;")
        assert "Table 'customers'" in schema
        # Orders is not referenced, so should be omitted
        assert "Table 'orders'" not in schema


def test_format_schema_fallback_on_unparsed():
    mock_tables = [
        {"name": "customers", "columns": [{"name": "id", "type": "int"}]},
        {"name": "orders", "columns": [{"name": "id", "type": "int"}]},
    ]
    with patch("app.db.get_schema", return_value=mock_tables):
        # Invalid syntax where sqlglot finds no tables -> should fall back to all tables
        schema = _format_schema_for_ai("MALFORMED SQL ???")
        assert "Table 'customers'" in schema
        assert "Table 'orders'" in schema


def test_ai_provider_missing_key_raises_error():
    with patch.dict("os.environ", {"AI_PROVIDER": "gemini", "GEMINI_API_KEY": "", "GEMINI_MODEL": "gemini-2.5-flash"}):
        with pytest.raises(AIProviderError) as exc_info:
            get_ai_provider()
        assert "GEMINI_API_KEY is missing" in str(exc_info.value)


def test_ai_provider_missing_model_raises_error():
    with patch.dict("os.environ", {"AI_PROVIDER": "gemini", "GEMINI_API_KEY": "fake_key", "GEMINI_MODEL": ""}):
        with pytest.raises(AIProviderError) as exc_info:
            get_ai_provider()
        assert "GEMINI_MODEL is missing" in str(exc_info.value)
