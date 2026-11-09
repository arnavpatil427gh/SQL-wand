import { useState, useRef, useMemo, useEffect } from "react";
import type { QueryError, SchemaTable, ValidationStatus } from "../api";
import { streamAiExplain, streamAiFix, streamAiOptimize, streamAiChat } from "../api";

type Props = {
  currentSql: string;
  lastError: QueryError | null;
  schema: SchemaTable[];
  onOpenDiff: (fixedSql: string) => void;
};

export type ChatItem = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: string[];
  validation?: ValidationStatus | null;
  fixedSql?: string | null;
};

/**
 * Extracts SQL code from markdown ```sql ... ``` block
 */
function extractSqlFromMarkdown(text: string): string | null {
  const match = text.match(/```(?:sql)?\s*([\s\S]*?)```/i);
  return match ? match[1].trim() : null;
}

/**
 * Safe markdown renderer for assistant responses.
 * Renders paragraphs, bold, lists, and code blocks.
 */
function MarkdownRenderer({ content }: { content: string }) {
  const blocks = useMemo(() => {
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

      const lines = part.split("\n");
      return (
        <div key={i} className="ai-text-block">
          {lines.map((line, lIdx) => {
            const trimmed = line.trim();
            if (!trimmed) return <div key={lIdx} className="ai-empty-line" />;

            if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
              return (
                <li key={lIdx} className="ai-list-item">
                  {renderInlineFormatting(trimmed.substring(2))}
                </li>
              );
            }

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

export default function AiPanel({ currentSql, lastError, schema, onOpenDiff }: Props) {
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [allowSampling, setAllowSampling] = useState<boolean>(false);
  const [inputPrompt, setInputPrompt] = useState<string>("");

  // In-flight streaming state
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [streamingResponse, setStreamingResponse] = useState<string>("");
  const [streamingTools, setStreamingTools] = useState<string[]>([]);
  const [streamingValidation, setStreamingValidation] = useState<ValidationStatus | null>(null);
  const [activeAction, setActiveAction] = useState<"explain" | "fix" | "optimize" | "chat" | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const chatContainerRef = useRef<HTMLDivElement | null>(null);
  const userScrolledUpRef = useRef<boolean>(false);

  // Monitor user scrolling: if they scroll up to read, don't force snap them to bottom
  const handleScroll = () => {
    if (!chatContainerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 60;
    userScrolledUpRef.current = !isNearBottom;
  };

  useEffect(() => {
    if (!userScrolledUpRef.current && chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, [messages, streamingResponse, streamingTools]);

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsLoading(false);
  };

  const executeStream = async (
    action: "explain" | "fix" | "optimize" | "chat",
    userPromptText?: string,
  ) => {
    handleStop();
    userScrolledUpRef.current = false;

    const controller = new AbortController();
    abortControllerRef.current = controller;

    setErrorMsg(null);
    setIsLoading(true);
    setActiveAction(action);
    setStreamingResponse("");
    setStreamingTools([]);
    setStreamingValidation(null);

    // If chat or quick action, append user turn to messages list
    const userMessage: ChatItem = {
      id: "u-" + Date.now(),
      role: "user",
      content:
        userPromptText ||
        (action === "explain"
          ? "Explain this query in simple terms."
          : action === "fix"
          ? "Help me fix this query error."
          : "Analyze this query and suggest optimizations."),
    };

    const nextHistory = [...messages, userMessage];
    setMessages(nextHistory);

    let accumulatedText = "";
    const accumulatedTools: string[] = [];
    let capturedValidation: ValidationStatus | null = null;

    const callbacks = {
      onToolCall: (msg: string) => {
        accumulatedTools.push(msg);
        setStreamingTools([...accumulatedTools]);
      },
      onValidation: (status: ValidationStatus) => {
        capturedValidation = status;
        setStreamingValidation(status);
      },
      onChunk: (chunk: string) => {
        accumulatedText += chunk;
        setStreamingResponse(accumulatedText);
      },
      onError: (err: string) => {
        setErrorMsg(err);
      },
      onDone: () => {
        setIsLoading(false);
        abortControllerRef.current = null;

        if (accumulatedText.trim()) {
          const fixedSql = extractSqlFromMarkdown(accumulatedText);
          const assistantMessage: ChatItem = {
            id: "a-" + Date.now(),
            role: "assistant",
            content: accumulatedText,
            toolCalls: accumulatedTools.length > 0 ? accumulatedTools : undefined,
            validation: capturedValidation,
            fixedSql,
          };
          setMessages((prev) => [...prev, assistantMessage]);
        }
        setStreamingResponse("");
        setStreamingTools([]);
        setStreamingValidation(null);
        setActiveAction(null);
      },
    };

    if (action === "explain") {
      await streamAiExplain(currentSql, allowSampling, callbacks, controller.signal, schema);
    } else if (action === "optimize") {
      await streamAiOptimize(currentSql, allowSampling, callbacks, controller.signal, schema);
    } else if (action === "fix") {
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
          allow_sampling: allowSampling,
        },
        callbacks,
        controller.signal,
      );
    } else if (action === "chat") {
      // Send chat history to backend
      const historyPayload = nextHistory.map((m) => ({
        role: m.role,
        content: m.content,
      }));
      await streamAiChat(
        {
          sql: currentSql,
          messages: historyPayload,
          allow_sampling: allowSampling,
        },
        callbacks,
        controller.signal,
      );
    }
  };

  const handleSendFollowUp = (e: React.FormEvent) => {
    e.preventDefault();
    const prompt = inputPrompt.trim();
    if (!prompt || isLoading) return;
    setInputPrompt("");
    executeStream("chat", prompt);
  };

  return (
    <aside className="ai-panel">
      <div className="ai-panel-header">
        <div>
          <h2>AI Assistant</h2>
          <span className="ai-subtitle">Gemini with Tool Use & Plan Verification</span>
        </div>
        <span className="ai-badge">Gemini</span>
      </div>

      {/* Quick Action Toolbar */}
      <div className="ai-toolbar">
        <button
          className="ai-btn"
          disabled={isLoading || !currentSql.trim()}
          onClick={() => executeStream("explain")}
          title="Explain current query in plain language"
        >
          Explain
        </button>
        <button
          className="ai-btn fix-btn"
          disabled={isLoading || !lastError}
          onClick={() => executeStream("fix")}
          title={lastError ? "Diagnose error and generate validated fix" : "Run query first to generate an error"}
        >
          Fix Error
        </button>
        <button
          className="ai-btn opt-btn"
          disabled={isLoading || !currentSql.trim()}
          onClick={() => executeStream("optimize")}
          title="Analyze EXPLAIN plan and recommend performance improvements"
        >
          Optimize
        </button>
        {isLoading && (
          <button className="ai-btn cancel-btn" onClick={handleStop}>
            Stop
          </button>
        )}
      </div>

      {/* Opt-in Toggle */}
      <div className="ai-optin-box">
        <label className="ai-optin-checkbox">
          <input
            type="checkbox"
            checked={allowSampling}
            onChange={(e) => setAllowSampling(e.target.checked)}
          />
          <span>Allow AI to sample column values</span>
        </label>
        <p className="ai-optin-hint">
          Inspects up to 3 non-null values from a column to debug types and formats. Never sends row collections.
        </p>
      </div>

      {/* Messages Scroll Area */}
      <div className="ai-content" ref={chatContainerRef} onScroll={handleScroll}>
        {messages.length === 0 && !isLoading && !errorMsg && (
          <div className="ai-placeholder">
            <p><strong>Welcome to the SQL Assistant!</strong></p>
            <p>• Click <strong>Explain</strong> for a step-by-step walkthrough.</p>
            <p>• Click <strong>Fix Error</strong> after a failed query for a verified fix.</p>
            <p>• Click <strong>Optimize</strong> to examine EXPLAIN execution plans.</p>
            <p>• Or type any follow-up question below.</p>
          </div>
        )}

        {errorMsg && (
          <div className="ai-error-box">
            <strong>Assistant Notice:</strong>
            <p>{errorMsg}</p>
          </div>
        )}

        {/* Existing Messages */}
        {messages.map((msg) => (
          <div key={msg.id} className={`chat-bubble ${msg.role}`}>
            <div className="chat-bubble-header">
              <span className="chat-role-label">
                {msg.role === "user" ? "You" : "SQL Assistant"}
              </span>
            </div>

            {/* Tool activity badges */}
            {msg.toolCalls && msg.toolCalls.length > 0 && (
              <div className="ai-tool-activity-list">
                {msg.toolCalls.map((t, idx) => (
                  <div key={idx} className="ai-tool-tag">
                    <span className="tool-dot">⚡</span> {t}
                  </div>
                ))}
              </div>
            )}

            {/* Message Body */}
            <MarkdownRenderer content={msg.content} />

            {/* Validation Badge */}
            {msg.validation && (
              <div className={`validation-status ${msg.validation.validated ? "verified" : "warning"}`}>
                {msg.validation.validated ? (
                  <>
                    <span className="badge-icon">✓</span>
                    <strong>Verified:</strong> parses and plans successfully
                  </>
                ) : (
                  <>
                    <span className="badge-icon">⚠</span>
                    <strong>Warning:</strong> {msg.validation.error || "Query could not be verified by database engine"}
                  </>
                )}
              </div>
            )}

            {/* Review Diff Button */}
            {msg.fixedSql && (
              <div className="ai-diff-prompt">
                <button
                  className="primary apply-fix-btn"
                  onClick={() => onOpenDiff(msg.fixedSql!)}
                >
                  Review & Apply Fix (Diff)
                </button>
              </div>
            )}
          </div>
        ))}

        {/* In-Flight Streaming Bubble */}
        {isLoading && (
          <div className="chat-bubble assistant streaming">
            <div className="chat-bubble-header">
              <span className="chat-role-label">SQL Assistant</span>
              <span className="ai-status-pulse">
                <span className="spinner" /> {activeAction === "fix" ? "Validating fix..." : "Thinking..."}
              </span>
            </div>

            {/* Streaming tool activity */}
            {streamingTools.length > 0 && (
              <div className="ai-tool-activity-list">
                {streamingTools.map((t, idx) => (
                  <div key={idx} className="ai-tool-tag active">
                    <span className="tool-dot">⚡</span> {t}
                  </div>
                ))}
              </div>
            )}

            {streamingResponse && <MarkdownRenderer content={streamingResponse} />}

            {streamingValidation && (
              <div className={`validation-status ${streamingValidation.validated ? "verified" : "warning"}`}>
                {streamingValidation.validated ? (
                  <>
                    <span className="badge-icon">✓</span>
                    <strong>Verified:</strong> parses and plans successfully
                  </>
                ) : (
                  <>
                    <span className="badge-icon">⚠</span>
                    <strong>Warning:</strong> {streamingValidation.error || "Query could not be verified by database engine"}
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Follow-up Chat Input Bar */}
      <form onSubmit={handleSendFollowUp} className="ai-chat-input-bar">
        <input
          type="text"
          value={inputPrompt}
          onChange={(e) => setInputPrompt(e.target.value)}
          placeholder="Ask a follow-up question..."
          disabled={isLoading}
        />
        <button
          type="submit"
          className="send-btn"
          disabled={isLoading || !inputPrompt.trim()}
        >
          Send
        </button>
      </form>
    </aside>
  );
}
