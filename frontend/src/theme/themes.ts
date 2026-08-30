export const ACCENTS = {
  cyan: { label: "青", accent: "#22D3EE", hover: "#06B6D4", fg: "#041014", dim: "rgba(34,211,238,0.14)", glow: "rgba(34,211,238,0.45)" },
  emerald: { label: "绿", accent: "#34D399", hover: "#10B981", fg: "#04140e", dim: "rgba(52,211,153,0.14)", glow: "rgba(52,211,153,0.45)" },
  violet: { label: "紫", accent: "#A78BFA", hover: "#8B5CF6", fg: "#120a22", dim: "rgba(167,139,250,0.14)", glow: "rgba(167,139,250,0.45)" },
  amber: { label: "琥珀", accent: "#FBBF24", hover: "#F59E0B", fg: "#1a1203", dim: "rgba(251,191,36,0.14)", glow: "rgba(251,191,36,0.45)" },
} as const;

export type AccentId = keyof typeof ACCENTS;
export const DEFAULT_ACCENT: AccentId = "cyan";
export const ACCENT_STORAGE_KEY = "neharness-accent";

export function isAccentId(v: string): v is AccentId {
  return v in ACCENTS;
}

export function applyAccent(id: AccentId) {
  const t = ACCENTS[id];
  const root = document.documentElement;
  root.style.setProperty("--color-accent", t.accent);
  root.style.setProperty("--color-accent-hover", t.hover);
  root.style.setProperty("--color-accent-fg", t.fg);
  root.style.setProperty("--color-accent-dim", t.dim);
  root.style.setProperty("--color-accent-glow", t.glow);
  root.setAttribute("data-accent", id);
  try {
    localStorage.setItem(ACCENT_STORAGE_KEY, id);
  } catch {
    /* ignore */
  }
}

export function readStoredAccent(): AccentId {
  try {
    const v = localStorage.getItem(ACCENT_STORAGE_KEY) || "";
    if (isAccentId(v)) return v;
  } catch {
    /* ignore */
  }
  return DEFAULT_ACCENT;
}
