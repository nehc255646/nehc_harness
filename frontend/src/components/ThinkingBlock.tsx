import { useState } from "react";
import { IconChevron } from "./icons";

export default function ThinkingBlock({
  text,
  streaming,
}: {
  text?: string;
  streaming?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const has = Boolean(text) || Boolean(streaming);
  if (!has) return null;

  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-[11px] text-faint hover:text-muted"
        aria-expanded={open}
      >
        <span className={`transition ${open ? "rotate-90" : ""}`}>
          <IconChevron className="h-3 w-3" />
        </span>
        <span className={streaming ? "animate-pulse" : ""}>
          {streaming ? "思考中" : "已完成思考"}
        </span>
      </button>
      {open && (
        <pre className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap font-sans text-[12px] leading-5 text-faint">
          {text}
          {streaming && <span className="ml-0.5 animate-pulse text-accent">▍</span>}
        </pre>
      )}
    </div>
  );
}
