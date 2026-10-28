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
