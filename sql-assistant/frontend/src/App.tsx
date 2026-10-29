import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSchema, runQuery, formatQuery, QueryError, QueryResult, SchemaTable } from "./api";
import SqlEditor from "./components/SqlEditor";
import ResultsGrid from "./components/ResultsGrid";
import SchemaSidebar from "./components/SchemaSidebar";
import AiPanel from "./components/AiPanel";
import DiffModal from "./components/DiffModal";

export default function App() {
  const [sql, setSql] = useState("SELECT * FROM customers;");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<QueryError | null>(null);
  const [running, setRunning] = useState(false);
  const [tables, setTables] = useState<SchemaTable[]>([]);

  // Diff Modal State
  const [diffSuggestion, setDiffSuggestion] = useState<string | null>(null);

  const sqlRef = useRef(sql);
  sqlRef.current = sql;

  useEffect(() => {
    fetchSchema().then(setTables).catch(() => setTables([]));
  }, []);

  // ---- Run query ----
  const run = useCallback(async () => {
    setRunning(true);
    try {
      const { result, error } = await runQuery(sqlRef.current);
      setResult(result ?? null);
      setError(error ?? null);
    } catch {
      setResult(null);
      setError({ message: "Could not reach the backend at 127.0.0.1:8000." });
    } finally {
      setRunning(false);
    }
  }, []);

  // ---- Format SQL (Ctrl+Shift+F or button) ----
  const format = useCallback(async () => {
    try {
      const formatted = await formatQuery(sqlRef.current);
      setSql(formatted);
    } catch {
      // If the format endpoint is unreachable, leave the SQL as-is
    }
  }, []);

  // ---- User edits SQL in editor ----
  const handleSqlChange = useCallback((newSql: string) => {
    setSql(newSql);
    // Notice: We do NOT immediately clear runtime error here so that the user
    // can still click "Fix Error" in the AI Assistant if they want guidance on the failure.
  }, []);

  // ---- Accept suggested fix from Diff modal (Rule 3: NEVER auto-runs) ----
  const handleAcceptFix = useCallback(() => {
    if (diffSuggestion !== null) {
      setSql(diffSuggestion);
      setDiffSuggestion(null);
      setError(null);
    }
  }, [diffSuggestion]);

  return (
    <div className="layout">
      <SchemaSidebar tables={tables} />
      <main>
        <div className="toolbar">
          <button onClick={run} disabled={running}>
            {running ? "Running…" : "Run (Ctrl+Enter)"}
          </button>
          <button className="secondary" onClick={format}>
            Format (Ctrl+Shift+F)
          </button>
          {result && (
            <span className="meta">
              {result.rows.length} rows in {result.elapsed_ms} ms
              {result.truncated && " (truncated, showing the first rows only)"}
            </span>
          )}
        </div>

        <div className="editor">
          <SqlEditor
            value={sql}
            onChange={handleSqlChange}
            onRun={run}
            onFormat={format}
            schema={tables}
            runtimeError={error}
          />
        </div>

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

      {/* AI Assistant Side Panel */}
      <AiPanel
        currentSql={sql}
        lastError={error}
        onOpenDiff={(fixedSql) => setDiffSuggestion(fixedSql)}
      />

      {/* Monaco Diff Modal for reviewing AI suggestions */}
      {diffSuggestion !== null && (
        <DiffModal
          original={sql}
          modified={diffSuggestion}
          onAccept={handleAcceptFix}
          onCancel={() => setDiffSuggestion(null)}
        />
      )}
    </div>
  );
}
