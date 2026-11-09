"""Database adapter. Keeping this small makes adding MySQL later easy."""
import os
import time

import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app_readonly:readonly_pw@127.0.0.1:5432/sampledb")
QUERY_TIMEOUT_MS = int(os.getenv("QUERY_TIMEOUT_MS", "5000"))
ROW_LIMIT = int(os.getenv("ROW_LIMIT", "500"))


class QueryError(Exception):
    def __init__(self, message, position=None, hint=None):
        super().__init__(message)
        self.message = message
        self.position = position  # 1-based character offset in the SQL
        self.hint = hint


# def _connect():
#     conn = psycopg.connect(DATABASE_URL)
#     conn.read_only = True  # read-only transaction, on top of the read-only role
#     return conn

def _connect():
    conn = psycopg.connect(DATABASE_URL, connect_timeout=5)
    conn.read_only = True
    return conn


def run_query(sql: str) -> dict:
    start = time.perf_counter()
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
            cur.execute(sql)
            if cur.description is None:
                return {"columns": [], "rows": [], "truncated": False, "elapsed_ms": 0}
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(ROW_LIMIT + 1)
            truncated = len(rows) > ROW_LIMIT
            rows = [list(r) for r in rows[:ROW_LIMIT]]
    except psycopg.Error as e:
        diag = e.diag
        position = int(diag.statement_position) if diag.statement_position else None
        raise QueryError(diag.message_primary or str(e), position, diag.message_hint)
    elapsed = round((time.perf_counter() - start) * 1000, 1)
    return {"columns": columns, "rows": rows, "truncated": truncated, "elapsed_ms": elapsed}


def get_schema() -> list[dict]:
    sql = """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        tables: dict[str, list[dict]] = {}
        for table, column, dtype in cur.fetchall():
            tables.setdefault(table, []).append({"name": column, "type": dtype})
    return [{"name": t, "columns": cols} for t, cols in tables.items()]


def explain_query(sql_text: str) -> str:
    """Run EXPLAIN (FORMAT TEXT) on a query using the read-only connection."""
    clean_sql = sql_text.strip().rstrip(";")
    if not clean_sql:
        return "No query provided to explain."
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
            cur.execute(f"EXPLAIN (FORMAT TEXT) {clean_sql}")
            lines = [row[0] for row in cur.fetchall()]
            # Cap output to avoid token explosion
            return "\n".join(lines[:40])
    except psycopg.Error as e:
        diag = e.diag
        msg = diag.message_primary or str(e)
        raise QueryError(f"EXPLAIN failed: {msg}", diag.statement_position, diag.message_hint)


def get_single_table_schema(table_name: str) -> list[dict]:
    """Get columns for a validated table in public schema."""
    sql_check = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql_check, (table_name.lower(),))
        rows = cur.fetchall()
        if not rows:
            raise ValueError(f"Table '{table_name}' does not exist in schema.")
        return [{"name": col, "type": dtype} for col, dtype in rows]


def sample_column_values(table_name: str, column_name: str, limit: int = 3) -> list:
    """Sample up to 3 non-null sanitized values from a column (strictly opt-in)."""
    # 1. Validate table and column exist in schema to prevent injection / invalid identifier
    col_info = get_single_table_schema(table_name)
    valid_cols = {c["name"].lower() for c in col_info}
    if column_name.lower() not in valid_cols:
        raise ValueError(f"Column '{column_name}' does not exist in table '{table_name}'.")

    # 2. Capped limit: at most 3 values
    capped_limit = max(1, min(limit, 3))

    # 3. Safe parameterised query with psycopg.sql identifiers
    query = psycopg.sql.SQL(
        "SELECT {col} FROM {tbl} WHERE {col} IS NOT NULL LIMIT {lim}"
    ).format(
        col=psycopg.sql.Identifier(column_name.lower()),
        tbl=psycopg.sql.Identifier(table_name.lower()),
        lim=psycopg.sql.Literal(capped_limit)
    )

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
        cur.execute(query)
        rows = cur.fetchall()
        # Truncate strings to max 40 chars to prevent leaking sensitive or excessive data
        sampled = []
        for r in rows:
            val = r[0]
            if isinstance(val, str) and len(val) > 40:
                sampled.append(val[:37] + "...")
            else:
                sampled.append(str(val))
        return sampled

