import { useState, useRef, useMemo } from "react";
import type { QueryError } from "../api";
import { streamAiExplain, streamAiFix } from "../api";

type Props = {
  currentSql: string;
  lastError: QueryError | null;
  onOpenDiff: (fixedSql: string) => void;
};

/**
 * Extracts SQL code from markdown ```sql ... ``` block
 */
function extractSqlFromMarkdown(text: string): string | null {
  const match = text.match(/```(?:sql)?\s*([\s\S]*?)```/i);
  return match ? match[1].trim() : null;
}

/**
 * Lightweight safe markdown renderer for assistant responses.
 * Renders paragraphs, bold, lists, and code blocks.
 */
function MarkdownRenderer({ content }: { content: string }) {
  const blocks = useMemo(() => {
    // Split on code blocks
    const parts = content.split(/(```[\s\S]*?```)/g);
    return parts.map((part, i) => {
      if (part.startsWith("```")) {
        const lines = part.slice(3, -3).replace(/^sql\s*/i, "").trim();
        return (
          <pre key={i} className="ai-code-block">
            <code>{lines}</code>
          </pre>
        );
      }

      // Render regular markdown paragraphs/lines
      const lines = part.split("\n");
      return (
        <div key={i} className="ai-text-block">
          {lines.map((line, lIdx) => {
            const trimmed = line.trim();
            if (!trimmed) return <div key={lIdx} className="ai-empty-line" />;

            // Bullet points
            if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
              return (
                <li key={lIdx} className="ai-list-item">
                  {renderInlineFormatting(trimmed.substring(2))}
                </li>
              );
            }

            // Headings
            if (trimmed.startsWith("### ")) {
              return <h4 key={lIdx}>{trimmed.substring(4)}</h4>;
            }
            if (trimmed.startsWith("## ") || trimmed.startsWith("# ")) {
              return <h3 key={lIdx}>{trimmed.replace(/^#+\s*/, "")}</h3>;
            }

            return <p key={lIdx}>{renderInlineFormatting(line)}</p>;
          })}
        </div>
      );
    });
  }, [content]);

  return <div className="ai-markdown">{blocks}</div>;
}

function renderInlineFormatting(text: string) {
  // Support `inline code` and **bold**
  const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return tokens.map((tok, idx) => {
    if (tok.startsWith("`") && tok.endsWith("`")) {
      return <code key={idx}>{tok.slice(1, -1)}</code>;
    }
    if (tok.startsWith("**") && tok.endsWith("**")) {
      return <strong key={idx}>{tok.slice(2, -2)}</strong>;
    }
    return tok;
  });
}

export default function AiPanel({ currentSql, lastError, onOpenDiff }: Props) {
  const [response, setResponse] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [activeTask, setActiveTask] = useState<"explain" | "fix" | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsLoading(false);
  };

  const startStream = async (type: "explain" | "fix") => {
    handleStop();

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setResponse("");
    setErrorMsg(null);
    setIsLoading(true);
    setActiveTask(type);

    const callbacks = {
      onChunk: (chunk: string) => {
        setResponse((prev) => prev + chunk);
      },
      onError: (err: string) => {
        setErrorMsg(err);
      },
      onDone: () => {
        setIsLoading(false);
        abortControllerRef.current = null;
      },
    };

    if (type === "explain") {
      await streamAiExplain(currentSql, callbacks, controller.signal);
    } else {
      if (!lastError) {
        setErrorMsg("No error details available to fix.");
        setIsLoading(false);
        return;
      }
      await streamAiFix(
        {
          sql: currentSql,
          error_message: lastError.message,
          error_position: lastError.position,
          error_hint: lastError.hint,
        },
        callbacks,
        controller.signal,
      );
    }
  };

  const detectedFixedSql = useMemo(() => {
    return extractSqlFromMarkdown(response);
  }, [response]);

  return (
    <aside className="ai-panel">
      <div className="ai-panel-header">
        <h2>AI Assistant</h2>
        <span className="ai-badge">Gemini</span>
      </div>

      <div className="ai-toolbar">
        <button
          className="ai-btn"
          disabled={isLoading || !currentSql.trim()}
          onClick={() => startStream("explain")}
          title="Explain the current query in simple terms"
        >
          Explain Query
        </button>
        <button
          className="ai-btn fix-btn"
          disabled={isLoading || !lastError}
          onClick={() => startStream("fix")}
          title={lastError ? "Diagnose and fix the runtime error" : "Only enabled when a query error occurs"}
        >
          Fix Error
        </button>
        {isLoading && (
          <button className="ai-btn cancel-btn" onClick={handleStop}>
            Stop
          </button>
        )}
      </div>

      <div className="ai-content">
        {isLoading && !response && (
          <div className="ai-status">
            <span className="spinner" /> Thinking ({activeTask === "explain" ? "explaining query" : "diagnosing error"})...
          </div>
        )}

        {errorMsg && (
          <div className="ai-error-box">
            <strong>Assistant Notice:</strong>
            <p>{errorMsg}</p>
          </div>
        )}

        {response ? (
          <div className="ai-response-scroll">
            <MarkdownRenderer content={response} />

            {detectedFixedSql && (
              <div className="ai-diff-prompt">
                <button
                  className="primary apply-fix-btn"
                  onClick={() => onOpenDiff(detectedFixedSql)}
                >
                  Review & Apply Fix (Diff)
                </button>
              </div>
            )}
          </div>
        ) : (
          !isLoading && !errorMsg && (
            <div className="ai-placeholder">
              <p>Click <strong>Explain Query</strong> for a step-by-step breakdown.</p>
              <p>When a query fails, click <strong>Fix Error</strong> to debug and inspect fixes.</p>
            </div>
          )
        )}
      </div>
    </aside>
  );
}
