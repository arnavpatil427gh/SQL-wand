#!/usr/bin/env bash
# Scaffolds Phase 0 + Phase 1 of the SQL editor project.
# Usage: bash setup.sh   (creates ./sql-assistant)
set -e
mkdir -p sql-assistant && cd sql-assistant
mkdir -p backend/app db frontend/src/components

# ---------------------------------------------------------------- root files
cat > .gitignore <<'EOF'
node_modules/
__pycache__/
.venv/
.env
dist/
EOF

cat > .env.example <<'EOF'
# Read-only role used to run user queries (created in db/init.sql)
DATABASE_URL=postgresql://app_readonly:readonly_pw@localhost:5432/sampledb
QUERY_TIMEOUT_MS=5000
ROW_LIMIT=500
# Used from Phase 3 onwards; never expose to the frontend
ANTHROPIC_API_KEY=
EOF

cat > docker-compose.yml <<'EOF'
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: admin
      POSTGRES_PASSWORD: admin_pw
      POSTGRES_DB: sampledb
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
volumes:
  pgdata:
EOF

# ---------------------------------------------------------------- database
cat > db/init.sql <<'EOF'
-- Small sample schema. Swap in Pagila later for a richer demo.
CREATE TABLE customers (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL,
  country TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL
);

CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  customer_id INT REFERENCES customers(id),
  product_id INT REFERENCES products(id),
  quantity INT NOT NULL,
  ordered_at TIMESTAMP DEFAULT now()
);

INSERT INTO customers (name, email, country) VALUES
  ('Asha Verma', 'asha@example.com', 'India'),
  ('Liam Chen', 'liam@example.com', 'Singapore'),
  ('Sofia Rossi', 'sofia@example.com', 'Italy');
INSERT INTO products (name, price) VALUES
  ('Keyboard', 49.99), ('Monitor', 199.00), ('Mouse', 19.50);
INSERT INTO orders (customer_id, product_id, quantity) VALUES
  (1, 1, 2), (1, 3, 1), (2, 2, 1), (3, 1, 1), (3, 3, 4);

-- Read-only role: this is what the app uses to execute user queries
CREATE ROLE app_readonly LOGIN PASSWORD 'readonly_pw';
GRANT CONNECT ON DATABASE sampledb TO app_readonly;
GRANT USAGE ON SCHEMA public TO app_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO app_readonly;
EOF

# ---------------------------------------------------------------- backend
cat > backend/requirements.txt <<'EOF'
fastapi==0.115.*
uvicorn[standard]==0.32.*
psycopg[binary]==3.2.*
python-dotenv==1.0.*
EOF

touch backend/app/__init__.py

cat > backend/app/db.py <<'EOF'
"""Database adapter. Keeping this small makes adding MySQL later easy."""
import os
import time

import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app_readonly:readonly_pw@localhost:5432/sampledb")
QUERY_TIMEOUT_MS = int(os.getenv("QUERY_TIMEOUT_MS", "5000"))
ROW_LIMIT = int(os.getenv("ROW_LIMIT", "500"))


class QueryError(Exception):
    def __init__(self, message, position=None, hint=None):
        super().__init__(message)
        self.message = message
        self.position = position  # 1-based character offset in the SQL
        self.hint = hint


def _connect():
    conn = psycopg.connect(DATABASE_URL)
    conn.read_only = True  # read-only transaction, on top of the read-only role
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
EOF

cat > backend/app/main.py <<'EOF'
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db

app = FastAPI(title="SQL Assistant API")


class QueryRequest(BaseModel):
    sql: str


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
EOF

# ---------------------------------------------------------------- frontend
cat > frontend/package.json <<'EOF'
{
  "name": "sql-assistant-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build"
  },
  "dependencies": {
    "@monaco-editor/react": "^4.6.0",
    "@tanstack/react-table": "^8.20.5",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.4",
    "vite": "^5.4.0"
  }
}
EOF

cat > frontend/vite.config.ts <<'EOF'
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
EOF

cat > frontend/tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true,
    "noEmit": true
  },
  "include": ["src"]
}
EOF

cat > frontend/index.html <<'EOF'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>SQL Assistant</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
EOF

cat > frontend/src/main.tsx <<'EOF'
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
EOF

cat > frontend/src/api.ts <<'EOF'
export type QueryResult = {
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
  elapsed_ms: number;
};
export type QueryError = { message: string; position?: number | null; hint?: string | null };
export type SchemaTable = { name: string; columns: { name: string; type: string }[] };

export async function runQuery(sql: string): Promise<{ result?: QueryResult; error?: QueryError }> {
  const res = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql }),
  });
  const data = await res.json();
  return res.ok ? { result: data } : { error: data.error };
}

export async function fetchSchema(): Promise<SchemaTable[]> {
  const res = await fetch("/api/schema");
  return (await res.json()).tables;
}
EOF

cat > frontend/src/components/SqlEditor.tsx <<'EOF'
import Editor, { OnMount } from "@monaco-editor/react";
import { useRef } from "react";

type Props = { value: string; onChange: (v: string) => void; onRun: () => void };

export default function SqlEditor({ value, onChange, onRun }: Props) {
  // Keep the latest onRun so the Monaco command never uses a stale closure
  const runRef = useRef(onRun);
  runRef.current = onRun;

  const handleMount: OnMount = (editor, monaco) => {
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => runRef.current());
  };

  return (
    <Editor
      height="100%"
      language="sql"
      theme="vs-dark"
      value={value}
      onChange={(v) => onChange(v ?? "")}
      onMount={handleMount}
      options={{ minimap: { enabled: false }, fontSize: 14, scrollBeyondLastLine: false }}
    />
  );
}
EOF

cat > frontend/src/components/ResultsGrid.tsx <<'EOF'
import { useMemo } from "react";
import { ColumnDef, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import type { QueryResult } from "../api";

export default function ResultsGrid({ result }: { result: QueryResult }) {
  const columns = useMemo<ColumnDef<unknown[]>[]>(
    () =>
      result.columns.map((name, i) => ({
        header: name,
        accessorFn: (row) => row[i],
        cell: (c) => {
          const v = c.getValue();
          return v === null ? <span className="null">NULL</span> : String(v);
        },
      })),
    [result.columns]
  );
  const table = useReactTable({ data: result.rows, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <table>
      <thead>
        {table.getHeaderGroups().map((hg) => (
          <tr key={hg.id}>
            {hg.headers.map((h) => (
              <th key={h.id}>{flexRender(h.column.columnDef.header, h.getContext())}</th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id}>
            {row.getVisibleCells().map((cell) => (
              <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
EOF

cat > frontend/src/components/SchemaSidebar.tsx <<'EOF'
import { useState } from "react";
import type { SchemaTable } from "../api";

export default function SchemaSidebar({ tables }: { tables: SchemaTable[] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <aside className="sidebar">
      <h2>Tables</h2>
      {tables.map((t) => (
        <div key={t.name}>
          <button className="table-name" onClick={() => setOpen({ ...open, [t.name]: !open[t.name] })}>
            {open[t.name] ? "▾" : "▸"} {t.name}
          </button>
          {open[t.name] && (
            <ul>
              {t.columns.map((c) => (
                <li key={c.name}>
                  {c.name} <small>{c.type}</small>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </aside>
  );
}
EOF

cat > frontend/src/App.tsx <<'EOF'
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSchema, runQuery, QueryError, QueryResult, SchemaTable } from "./api";
import SqlEditor from "./components/SqlEditor";
import ResultsGrid from "./components/ResultsGrid";
import SchemaSidebar from "./components/SchemaSidebar";

export default function App() {
  const [sql, setSql] = useState("SELECT * FROM customers;");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<QueryError | null>(null);
  const [running, setRunning] = useState(false);
  const [tables, setTables] = useState<SchemaTable[]>([]);
  const sqlRef = useRef(sql);
  sqlRef.current = sql;

  useEffect(() => {
    fetchSchema().then(setTables).catch(() => setTables([]));
  }, []);

  const run = useCallback(async () => {
    setRunning(true);
    const { result, error } = await runQuery(sqlRef.current);
    setResult(result ?? null);
    setError(error ?? null);
    setRunning(false);
  }, []);

  return (
    <div className="layout">
      <SchemaSidebar tables={tables} />
      <main>
        <div className="toolbar">
          <button onClick={run} disabled={running}>{running ? "Running…" : "Run (Ctrl+Enter)"}</button>
          {result && (
            <span className="meta">
              {result.rows.length} rows in {result.elapsed_ms} ms
              {result.truncated && " (truncated, showing the first rows only)"}
            </span>
          )}
        </div>
        <div className="editor"><SqlEditor value={sql} onChange={setSql} onRun={run} /></div>
        <div className="results">
          {error && (
            <div className="error">
              <strong>{error.message}</strong>
              {error.position && <div>At character {error.position}</div>}
              {error.hint && <div>Hint: {error.hint}</div>}
            </div>
          )}
          {result && <ResultsGrid result={result} />}
          {!result && !error && <p className="empty">Write a query and press Ctrl+Enter to run it.</p>}
        </div>
      </main>
    </div>
  );
}
EOF

cat > frontend/src/styles.css <<'EOF'
:root { --bg:#1e1e1e; --panel:#252526; --border:#3c3c3c; --text:#d4d4d4; --accent:#3b82f6; --err:#f87171; }
* { box-sizing: border-box; }
html, body, #root { height: 100%; margin: 0; }
body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; }
.layout { display: grid; grid-template-columns: 240px 1fr; height: 100%; }
.sidebar { background: var(--panel); border-right: 1px solid var(--border); padding: 12px; overflow: auto; }
.sidebar h2 { font-size: 13px; margin: 0 0 8px; }
.sidebar ul { margin: 2px 0 8px 18px; padding: 0; list-style: none; }
.sidebar small { color: #888; margin-left: 4px; }
.table-name { background: none; border: 0; color: var(--text); cursor: pointer; padding: 3px 0; font: inherit; }
main { display: grid; grid-template-rows: auto 45% 1fr; min-width: 0; height: 100%; }
.toolbar { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
.toolbar button { background: var(--accent); color: #fff; border: 0; padding: 6px 14px; border-radius: 4px; cursor: pointer; }
.toolbar button:disabled { opacity: .6; cursor: wait; }
.meta { color: #999; }
.results { overflow: auto; border-top: 1px solid var(--border); }
.empty { padding: 12px; color: #888; }
.error { padding: 12px; color: var(--err); }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 6px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
th { position: sticky; top: 0; background: var(--panel); }
.null { color: #777; font-style: italic; }
EOF

# ---------------------------------------------------------------- README
cat > README.md <<'EOF'
# SQL Assistant

## Run it
1. Database:   `docker compose up -d`
2. Backend:    `cd backend && python -m venv .venv && source .venv/bin/activate`
               `pip install -r requirements.txt && uvicorn app.main:app --reload`
3. Frontend:   `cd frontend && npm install && npm run dev`
4. Open http://localhost:5173

(Optional) `cp .env.example .env` to override settings. On Windows, activate the venv with `.venv\Scripts\activate`.
EOF

echo "Done. See sql-assistant/README.md to run it."
