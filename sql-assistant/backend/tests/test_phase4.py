"""
Tests for Phase 4: Tools, EXPLAIN plan analysis, Fix Validation, and History Trimming.
"""

from unittest.mock import patch, MagicMock
import pytest

from app.main import (
    extract_sql_from_markdown,
    validate_sql_fix,
    trim_history,
    make_tool_executor,
    ChatMessage,
)
from app.ai import get_sql_tool_declarations


def test_extract_sql_from_markdown():
    text = "Here is the fix:\n```sql\nSELECT id, name FROM customers;\n```\nHope that helps!"
    assert extract_sql_from_markdown(text) == "SELECT id, name FROM customers;"

    text_no_fence = "Just write SELECT * FROM orders"
    assert extract_sql_from_markdown(text_no_fence) is None


def test_validate_sql_fix_syntax_error():
    is_valid, err = validate_sql_fix("SELEC broken syntax FROM")
    assert is_valid is False
    assert err is not None
    assert "Syntax error" in err


def test_validate_sql_fix_valid():
    with patch("app.db.explain_query", return_value="Seq Scan on customers"):
        is_valid, err = validate_sql_fix("SELECT id FROM customers WHERE id = 1;")
        assert is_valid is True
        assert err is None


def test_validate_sql_fix_db_error():
    from app.db import QueryError
    with patch("app.db.explain_query", side_effect=QueryError("column 'xyz' does not exist")):
        is_valid, err = validate_sql_fix("SELECT xyz FROM customers;")
        assert is_valid is False
        assert "column 'xyz' does not exist" in err


def test_tool_declarations_sampling_flag():
    tools_no_sampling = get_sql_tool_declarations(allow_sampling=False)
    decls_no_sampling = [f.name for f in tools_no_sampling[0].function_declarations]
    assert "get_table_schema" in decls_no_sampling
    assert "run_explain" in decls_no_sampling
    assert "sample_values" not in decls_no_sampling

    tools_with_sampling = get_sql_tool_declarations(allow_sampling=True)
    decls_with_sampling = [f.name for f in tools_with_sampling[0].function_declarations]
    assert "sample_values" in decls_with_sampling


def test_make_tool_executor_sampling_permission():
    executor_no_optin = make_tool_executor(allow_sampling=False)
    result = executor_no_optin("sample_values", {"table_name": "customers", "column_name": "name"})
    assert "error" in result
    assert "disabled" in result["error"].lower()

    executor_with_optin = make_tool_executor(allow_sampling=True)
    with patch("app.db.sample_column_values", return_value=["Alice", "Bob"]):
        res = executor_with_optin("sample_values", {"table_name": "customers", "column_name": "name"})
        assert res["samples"] == ["Alice", "Bob"]


def test_trim_history():
    short_msgs = [
        ChatMessage(role="user", content="Hello"),
        ChatMessage(role="assistant", content="Hi"),
    ]
    assert trim_history(short_msgs, max_chars=100) == short_msgs

    long_msgs = [
        ChatMessage(role="user", content="A" * 50),
        ChatMessage(role="assistant", content="B" * 50),
        ChatMessage(role="user", content="C" * 50),
    ]
    trimmed = trim_history(long_msgs, max_chars=70)
    assert len(trimmed) == 1
    assert trimmed[0].content == "C" * 50
