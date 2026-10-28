export type QueryResult = {
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
  elapsed_ms: number;
};
export type QueryError = { message: string; position?: number | null; hint?: string | null };
export type SchemaTable = { name: string; columns: { name: string; type: string }[] };

/** Shape returned by POST /api/analyze */
export type AnalyzeResult = {
  valid: boolean;
  errors: { message: string; line: number; column: number }[];
  tables: { name: string; schema: string | null; alias: string | null }[];
  columns: { name: string; table: string | null }[];
};

// ---------------------------------------------------------------------------
//  Existing endpoints
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
//  Phase 2 — SQL intelligence endpoints
// ---------------------------------------------------------------------------

/** Validate syntax and extract tables/columns (sqlglot-powered). */
export async function analyzeQuery(sql: string): Promise<AnalyzeResult> {
  const res = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql }),
  });
  return res.json();
}

/** Pretty-print SQL via sqlglot. Returns original on parse failure. */
export async function formatQuery(sql: string): Promise<string> {
  const res = await fetch("/api/format", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql }),
  });
  const data = await res.json();
  return data.sql;
}
