import Editor, { type OnMount } from "@monaco-editor/react";
import { useEffect, useRef } from "react";
import type { SchemaTable, AnalyzeResult, QueryError } from "../api";
import { analyzeQuery } from "../api";
import { offsetToMarkerRange } from "../utils/sqlHelpers";

// -----------------------------------------------------------------------
//  SQL keywords offered by the completion provider
// -----------------------------------------------------------------------
const SQL_KEYWORDS = [
  "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "EXISTS",
  "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "ON",
  "GROUP BY", "ORDER BY", "ASC", "DESC", "HAVING",
  "LIMIT", "OFFSET", "UNION", "ALL", "INTERSECT", "EXCEPT",
  "INSERT INTO", "VALUES", "UPDATE", "SET", "DELETE",
  "AS", "DISTINCT", "BETWEEN", "LIKE", "ILIKE", "IS", "NULL",
  "CASE", "WHEN", "THEN", "ELSE", "END",
  "COUNT", "SUM", "AVG", "MIN", "MAX",
  "WITH", "RETURNING", "TRUE", "FALSE", "COALESCE", "CAST",
];

// -----------------------------------------------------------------------
//  Component props
// -----------------------------------------------------------------------
type Props = {
  value: string;
  onChange: (v: string) => void;
  onRun: () => void;
  onFormat: () => void;
  /** Schema tables for autocompletion (same data as the sidebar). */
  schema: SchemaTable[];
  /** Most recent runtime Postgres error — used to place a red marker. */
  runtimeError?: QueryError | null;
};

// -----------------------------------------------------------------------
//  Derive the Monaco type from the OnMount callback signature
// -----------------------------------------------------------------------
type MonacoInstance = Parameters<OnMount>[1];
type EditorInstance = Parameters<OnMount>[0];

export default function SqlEditor({
  value,
  onChange,
  onRun,
  onFormat,
  schema,
  runtimeError,
}: Props) {
  // Keep the latest callbacks so Monaco commands never use stale closures
  const runRef = useRef(onRun);
  runRef.current = onRun;
  const formatRef = useRef(onFormat);
  formatRef.current = onFormat;

  // Refs to the Monaco instances — needed for markers & completions
  const editorRef = useRef<EditorInstance | null>(null);
  const monacoRef = useRef<MonacoInstance | null>(null);

  // Refs that the completion provider reads (always up-to-date)
  const schemaRef = useRef(schema);
  schemaRef.current = schema;
  const analyzeRef = useRef<AnalyzeResult | null>(null);

  // Disposable returned by registerCompletionItemProvider
  const disposableRef = useRef<{ dispose(): void } | null>(null);

  // ------------------------------------------------------------------
  //  Editor mount — register shortcuts & completion provider
  // ------------------------------------------------------------------
  const handleMount: OnMount = (editor, monaco) => {
    // Clean up a previous registration (React StrictMode double-mount)
    disposableRef.current?.dispose();

    editorRef.current = editor;
    monacoRef.current = monaco;

    // --- Keyboard shortcuts ---
    editor.addCommand(
      monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter,
      () => runRef.current(),
    );
    editor.addCommand(
      monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyF,
      () => formatRef.current(),
    );

    // --- Completion provider ---
    disposableRef.current = monaco.languages.registerCompletionItemProvider(
      "sql",
      {
        triggerCharacters: ["."],
        provideCompletionItems: (
          model: Parameters<NonNullable<Parameters<typeof monaco.languages.registerCompletionItemProvider>[1]["provideCompletionItems"]>>[0],
          position: Parameters<NonNullable<Parameters<typeof monaco.languages.registerCompletionItemProvider>[1]["provideCompletionItems"]>>[1]
        ) => {
          const word = model.getWordUntilPosition(position);
          const range = {
            startLineNumber: position.lineNumber,
            startColumn: word.startColumn,
            endLineNumber: position.lineNumber,
            endColumn: position.column,
          };

          // Text from the start of the line to the cursor
          const linePrefix = model.getValueInRange({
            startLineNumber: position.lineNumber,
            startColumn: 1,
            endLineNumber: position.lineNumber,
            endColumn: position.column,
          });

          // --- After "alias." or "table." → column suggestions ---
          const dotMatch = linePrefix.match(/\b(\w+)\.\w*$/);
          if (dotMatch) {
            const qualifier = dotMatch[1].toLowerCase();
            const currentAnalyze = analyzeRef.current;

            // Resolve alias → real table name using the latest analyze result
            let tableName = qualifier;
            if (currentAnalyze) {
              const aliased = currentAnalyze.tables.find(
                (t) => (t.alias ?? "").toLowerCase() === qualifier,
              );
              if (aliased) tableName = aliased.name.toLowerCase();
            }

            const table = schemaRef.current.find(
              (t) => t.name.toLowerCase() === tableName,
            );
            if (table) {
              return {
                suggestions: table.columns.map((col) => ({
                  label: col.name,
                  kind: monaco.languages.CompletionItemKind.Field,
                  detail: col.type,
                  insertText: col.name,
                  range,
                })),
              };
            }
          }

          // --- General suggestions: tables + keywords + in-scope columns ---
          const suggestions: {
            label: string;
            kind: number;
            detail?: string;
            insertText: string;
            range: typeof range;
          }[] = [];

          // Table names
          for (const t of schemaRef.current) {
            suggestions.push({
              label: t.name,
              kind: monaco.languages.CompletionItemKind.Class,
              detail: "table",
              insertText: t.name,
              range,
            });
          }

          // SQL keywords
          for (const kw of SQL_KEYWORDS) {
            suggestions.push({
              label: kw,
              kind: monaco.languages.CompletionItemKind.Keyword,
              insertText: kw,
              range,
            });
          }

          // Columns from tables that appear in the current query
          const currentAnalyze = analyzeRef.current;
          if (currentAnalyze) {
            const mentioned = new Set(
              currentAnalyze.tables.map((t) => t.name.toLowerCase()),
            );
            for (const table of schemaRef.current) {
              if (mentioned.has(table.name.toLowerCase())) {
                for (const col of table.columns) {
                  suggestions.push({
                    label: col.name,
                    kind: monaco.languages.CompletionItemKind.Field,
                    detail: `${table.name}.${col.name} (${col.type})`,
                    insertText: col.name,
                    range,
                  });
                }
              }
            }
          }

          return { suggestions };
        },
      },
    );
  };

  // ------------------------------------------------------------------
  //  Debounced syntax analysis (sqlglot) — sets red-underline markers
  // ------------------------------------------------------------------
  useEffect(() => {
    const m = monacoRef.current;
    const ed = editorRef.current;

    // Clear runtime markers whenever the user edits (stale position)
    if (m && ed) {
      const model = ed.getModel();
      if (model) m.editor.setModelMarkers(model, "runtime", []);
    }

    // Nothing to analyse for blank input
    if (!value.trim()) {
      if (m && ed) {
        const model = ed.getModel();
        if (model) m.editor.setModelMarkers(model, "sqlglot", []);
      }
      analyzeRef.current = null;
      return;
    }

    const timer = setTimeout(async () => {
      try {
        const result = await analyzeQuery(value);
        analyzeRef.current = result;

        if (!monacoRef.current || !editorRef.current) return;
        const model = editorRef.current.getModel();
        if (!model) return;

        const markers = result.errors.map((err) => {
          // Underline the token at the error position
          const lines = value.split("\n");
          const line = lines[err.line - 1] ?? "";
          let endCol = err.column;
          while (endCol - 1 < line.length && /\S/.test(line[endCol - 1])) {
            endCol++;
          }
          if (endCol <= err.column) endCol = err.column + 1;

          return {
            severity: monacoRef.current!.MarkerSeverity.Error,
            message: err.message,
            startLineNumber: err.line,
            startColumn: err.column,
            endLineNumber: err.line,
            endColumn: endCol,
          };
        });

        monacoRef.current.editor.setModelMarkers(model, "sqlglot", markers);
      } catch {
        // Backend unreachable — silently skip analysis
      }
    }, 500);

    return () => clearTimeout(timer);
  }, [value]);

  // ------------------------------------------------------------------
  //  Runtime Postgres error → red marker at the reported position
  // ------------------------------------------------------------------
  useEffect(() => {
    const m = monacoRef.current;
    const ed = editorRef.current;
    if (!m || !ed) return;
    const model = ed.getModel();
    if (!model) return;

    if (runtimeError?.position) {
      const range = offsetToMarkerRange(value, runtimeError.position);
      m.editor.setModelMarkers(model, "runtime", [
        {
          severity: m.MarkerSeverity.Error,
          message: runtimeError.message,
          ...range,
        },
      ]);
    } else {
      m.editor.setModelMarkers(model, "runtime", []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runtimeError]);

  // ------------------------------------------------------------------
  //  Cleanup on unmount
  // ------------------------------------------------------------------
  useEffect(() => {
    return () => disposableRef.current?.dispose();
  }, []);

  // ------------------------------------------------------------------
  //  Render
  // ------------------------------------------------------------------
  return (
    <Editor
      height="100%"
      language="sql"
      theme="vs-dark"
      value={value}
      onChange={(v) => onChange(v ?? "")}
      onMount={handleMount}
      options={{
        minimap: { enabled: false },
        fontSize: 14,
        scrollBeyondLastLine: false,
        quickSuggestions: true,
        suggestOnTriggerCharacters: true,
      }}
    />
  );
}
