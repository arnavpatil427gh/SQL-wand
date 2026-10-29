import { DiffEditor } from "@monaco-editor/react";

type Props = {
  original: string;
  modified: string;
  onAccept: () => void;
  onCancel: () => void;
};

export default function DiffModal({ original, modified, onAccept, onCancel }: Props) {
  return (
    <div className="diff-modal-backdrop">
      <div className="diff-modal">
        <div className="diff-modal-header">
          <div>
            <h3>Review Suggested Query Changes</h3>
            <p className="diff-subtitle">Left: Your current query | Right: AI suggestion</p>
          </div>
          <div className="diff-actions">
            <button className="secondary" onClick={onCancel}>
              Cancel
            </button>
            <button className="primary" onClick={onAccept}>
              Accept Fix (Does Not Auto-Run)
            </button>
          </div>
        </div>
        <div className="diff-modal-body">
          <DiffEditor
            original={original}
            modified={modified}
            language="sql"
            theme="vs-dark"
            options={{
              readOnly: true,
              renderSideBySide: true,
              minimap: { enabled: false },
              fontSize: 13,
              automaticLayout: true,
            }}
          />
        </div>
      </div>
    </div>
  );
}
