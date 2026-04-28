import { useState } from "react";

interface Props {
  title: string;
  body: any;
  ok?: boolean;
}

export function ToolCallExpando({ title, body, ok }: Props) {
  const [open, setOpen] = useState(false);
  const indicator = ok === undefined ? "·" : ok ? "✓" : "✗";
  const cls = ok === false ? "tool-card tool-card-fail" : "tool-card";
  return (
    <div className={cls}>
      <button className="tool-card-header" onClick={() => setOpen((o) => !o)}>
        <span className="tool-indicator">{indicator}</span>
        <span>{title}</span>
        <span className="tool-chevron">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <pre className="tool-card-body">
          {typeof body === "string" ? body : JSON.stringify(body, null, 2)}
        </pre>
      )}
    </div>
  );
}
