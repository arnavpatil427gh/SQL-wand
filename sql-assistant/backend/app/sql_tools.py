"""
SQL analysis tools powered by sqlglot.

All functions are pure (no DB connection) and dialect-parameterised so
adding MySQL later is just a matter of passing dialect="mysql".
Every function catches parse failures gracefully — the editor should
never break because of a half-typed query.
"""

import sqlglot
from sqlglot import exp, errors as sqlglot_errors


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(sql: str, dialect: str = "postgres") -> list[dict]:
    """
    Parse *sql* and return a list of syntax-error dicts.

    Each dict has:
        message  – human-readable description
        line     – 1-based line number
        column   – 1-based column number

    Returns [] when the SQL is syntactically valid.
    """
    if not sql.strip():
        return []
    try:
        sqlglot.transpile(sql, read=dialect, error_level=sqlglot_errors.ErrorLevel.RAISE)
        return []
    except sqlglot_errors.ParseError as exc:
        result = []
        for err in exc.errors:
            result.append({
                "message": err.get("description", str(exc)),
                "line": err.get("line", 1),
                "column": err.get("col", 1),
            })
        # Fallback if the error list was empty but we still got an exception
        return result if result else [{"message": str(exc), "line": 1, "column": 1}]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_sql(sql: str, dialect: str = "postgres") -> str:
    """
    Pretty-print *sql*.  Returns the original string unchanged when
    sqlglot cannot parse it (so the user never loses their work).
    """
    if not sql.strip():
        return sql
    try:
        parts = sqlglot.transpile(sql, read=dialect, write=dialect, pretty=True)
        return "\n\n".join(parts)
    except sqlglot_errors.ParseError:
        return sql


# ---------------------------------------------------------------------------
# Table / column extraction
# ---------------------------------------------------------------------------

def extract_tables_and_columns(sql: str, dialect: str = "postgres") -> dict:
    """
    Walk the AST and pull out every referenced table (with alias) and
    every column reference (with its table qualifier, if any).

    Robust to CTEs, sub-queries, and aliased JOINs.
    On invalid SQL it returns whatever it can (possibly empty lists).

    Returns::

        {
            "tables":  [{"name": "orders", "schema": null, "alias": "o"}, ...],
            "columns": [{"name": "id", "table": "o"}, ...]
        }
    """
    tables: list[dict] = []
    columns: list[dict] = []

    if not sql.strip():
        return {"tables": tables, "columns": columns}

    try:
        # IGNORE-level so partial / broken SQL still yields a partial AST
        statements = sqlglot.parse(sql, read=dialect, error_level=sqlglot_errors.ErrorLevel.IGNORE)

        for statement in statements:
            if statement is None:
                continue

            # ---- tables (includes CTE references & sub-query aliases) ----
            for tbl in statement.find_all(exp.Table):
                name = tbl.name
                if not name:
                    continue
                tables.append({
                    "name": name,
                    "schema": tbl.db or None,
                    "alias": tbl.alias or None,
                })

            # ---- column references ----
            for col in statement.find_all(exp.Column):
                name = col.name
                if not name:
                    continue
                columns.append({
                    "name": name,
                    "table": col.table or None,  # qualifier before the dot
                })

    except Exception:
        # Absolute safety net — never crash the endpoint.
        pass

    # De-duplicate tables (same name+alias pair seen more than once)
    seen: set[tuple[str, str | None]] = set()
    unique_tables: list[dict] = []
    for t in tables:
        key = (t["name"], t["alias"])
        if key not in seen:
            seen.add(key)
            unique_tables.append(t)

    return {"tables": unique_tables, "columns": columns}
