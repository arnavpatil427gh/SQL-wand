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
  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    });
    const data = await res.json();
    return res.ok ? { result: data } : { error: data.error };
  } catch (err) {
    return {
      error: { message: "Could not reach the backend at 127.0.0.1:8000. Is the server running?" },
    };
  }
}

export async function fetchSchema(): Promise<SchemaTable[]> {
  try {
    const res = await fetch("/api/schema");
    if (!res.ok) return [];
    const data = await res.json();
    return data.tables || [];
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
//  Phase 2 — SQL intelligence endpoints
// ---------------------------------------------------------------------------

/** Validate syntax and extract tables/columns (sqlglot-powered). */
export async function analyzeQuery(sql: string): Promise<AnalyzeResult> {
  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    });
    return await res.json();
  } catch {
    return { valid: true, errors: [], tables: [], columns: [] };
  }
}

/** Pretty-print SQL via sqlglot. Returns original on parse failure. */
export async function formatQuery(sql: string): Promise<string> {
  try {
    const res = await fetch("/api/format", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    });
    const data = await res.json();
    return data.sql || sql;
  } catch {
    return sql;
  }
}

// ---------------------------------------------------------------------------
//  Phase 3 — AI Assistant SSE Stream Helpers
// ---------------------------------------------------------------------------

export type StreamCallbacks = {
  onChunk: (chunk: string) => void;
  onError: (error: string) => void;
  onDone: () => void;
};

async function streamPost(
  url: string,
  body: unknown,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });

    if (!res.ok) {
      const errText = await res.text();
      try {
        const parsed = JSON.parse(errText);
        callbacks.onError(parsed.error?.message || parsed.error || `HTTP error ${res.status}`);
      } catch {
        callbacks.onError(`HTTP error ${res.status}`);
      }
      callbacks.onDone();
      return;
    }

    if (!res.body) {
      callbacks.onError("No response stream received from backend.");
      callbacks.onDone();
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      // Keep incomplete fragment in buffer
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const jsonStr = trimmed.slice(5).trim();
        if (!jsonStr) continue;

        try {
          const payload = JSON.parse(jsonStr);
          if (payload.error) {
            callbacks.onError(payload.error);
          } else if (payload.text) {
            callbacks.onChunk(payload.text);
          } else if (payload.done) {
            callbacks.onDone();
          }
        } catch (e) {
          console.warn("Failed to parse SSE payload", jsonStr, e);
        }
      }
    }

    callbacks.onDone();
  } catch (err: unknown) {
    if ((err as Error)?.name === "AbortError") {
      // User aborted stream
      callbacks.onDone();
      return;
    }
    callbacks.onError((err as Error)?.message || "Failed to communicate with AI endpoint.");
    callbacks.onDone();
  }
}

export function streamAiExplain(
  sql: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  return streamPost("/api/ai/explain", { sql }, callbacks, signal);
}

export function streamAiFix(
  params: {
    sql: string;
    error_message: string;
    error_position?: number | null;
    error_hint?: string | null;
  },
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  return streamPost("/api/ai/fix", params, callbacks, signal);
}
