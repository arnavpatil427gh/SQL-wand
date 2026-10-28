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
