/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: {
          DEFAULT: "var(--color-accent)",
          hover: "var(--color-accent-hover)",
          fg: "var(--color-accent-fg)",
          dim: "var(--color-accent-dim)",
          glow: "var(--color-accent-glow)",
        },
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        "surface-2": "var(--color-surface-2)",
        muted: "var(--color-muted)",
        faint: "var(--color-faint)",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', '"Noto Sans SC"', "PingFang SC", "Microsoft YaHei", "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 rgba(255,255,255,0.04) inset, 0 12px 40px rgba(0,0,0,0.35)",
      },
    },
  },
  plugins: [],
};
