"""
Tests for sql_tools — run with:
    cd backend && python -m pytest tests/ -v
"""

import pytest
from app.sql_tools import validate, format_sql, extract_tables_and_columns


# -----------------------------------------------------------------------
#  validate()
# -----------------------------------------------------------------------

class TestValidate:
    """Syntax-level validation via sqlglot."""

    def test_valid_simple_select(self):
        assert validate("SELECT id, name FROM customers") == []

    def test_valid_select_star(self):
        assert validate("SELECT * FROM orders LIMIT 10") == []

    def test_valid_join(self):
        sql = (
            "SELECT o.id, c.name "
            "FROM orders o "
            "JOIN customers c ON o.customer_id = c.id"
        )
        assert validate(sql) == []

    def test_valid_cte(self):
        sql = """
        WITH recent AS (
            SELECT * FROM orders WHERE order_date > '2024-01-01'
        )
        SELECT * FROM recent
        """
        assert validate(sql) == []

    def test_broken_keyword_returns_errors(self):
        errs = validate("SELEC id FROM customers")
        assert len(errs) >= 1
        assert "line" in errs[0] and "column" in errs[0]

    def test_missing_from_clause(self):
        errs = validate("SELECT id WHERE x = 1")
        # sqlglot may or may not flag this; at minimum it should not crash
        assert isinstance(errs, list)

    def test_empty_string_returns_no_errors(self):
        assert validate("") == []

    def test_whitespace_only(self):
        assert validate("   \n  ") == []


# -----------------------------------------------------------------------
#  format_sql()
# -----------------------------------------------------------------------

class TestFormatSql:
    """Pretty-printing via sqlglot."""

    def test_formats_compact_to_multiline(self):
        result = format_sql("select id,name from customers where id=1")
        assert "SELECT" in result
        assert "FROM" in result
        assert "\n" in result  # should be multi-line

    def test_broken_sql_returns_original(self):
        original = "SELEC broken stuff"
        assert format_sql(original) == original

    def test_empty_string(self):
        assert format_sql("") == ""

    def test_multiple_statements(self):
        sql = "SELECT 1; SELECT 2"
        result = format_sql(sql)
        # Both statements should appear in the output
        assert "1" in result
        assert "2" in result


# -----------------------------------------------------------------------
#  extract_tables_and_columns()
# -----------------------------------------------------------------------

class TestExtractTablesAndColumns:
    """AST-level extraction of table and column references."""

    def test_simple_select(self):
        info = extract_tables_and_columns("SELECT id, name FROM customers")
        table_names = [t["name"] for t in info["tables"]]
        assert "customers" in table_names
        col_names = [c["name"] for c in info["columns"]]
        assert "id" in col_names
        assert "name" in col_names

    def test_join_with_aliases(self):
        sql = """
        SELECT o.id, c.name, p.title
        FROM orders o
        JOIN customers c ON o.customer_id = c.id
        JOIN products p ON o.product_id = p.id
        """
        info = extract_tables_and_columns(sql)
        table_names = [t["name"] for t in info["tables"]]
        assert "orders" in table_names
        assert "customers" in table_names
        assert "products" in table_names
        # Check alias resolution
        alias_map = {t["name"]: t["alias"] for t in info["tables"]}
        assert alias_map["orders"] == "o"
        assert alias_map["customers"] == "c"
        assert alias_map["products"] == "p"

    def test_cte_extracts_both_cte_body_and_usage(self):
        sql = """
        WITH active_customers AS (
            SELECT id, name FROM customers WHERE active = true
        )
        SELECT * FROM active_customers
        """
        info = extract_tables_and_columns(sql)
        table_names = [t["name"] for t in info["tables"]]
        assert "customers" in table_names
        assert "active_customers" in table_names

    def test_subquery_alias(self):
        sql = """
        SELECT sub.total
        FROM (SELECT COUNT(*) AS total FROM orders) sub
        """
        info = extract_tables_and_columns(sql)
        table_names = [t["name"] for t in info["tables"]]
        assert "orders" in table_names

    def test_broken_sql_does_not_crash(self):
        info = extract_tables_and_columns("SELEC broken stuff FROM ???")
        assert "tables" in info
        assert "columns" in info

    def test_empty_string(self):
        info = extract_tables_and_columns("")
        assert info == {"tables": [], "columns": []}

    def test_deduplicates_tables(self):
        sql = "SELECT * FROM customers c WHERE c.id IN (SELECT customer_id FROM customers)"
        info = extract_tables_and_columns(sql)
        # "customers" appears twice in the SQL but may have different aliases,
        # so we just verify no exact duplicates
        keys = [(t["name"], t["alias"]) for t in info["tables"]]
        assert len(keys) == len(set(keys))
