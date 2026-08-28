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
      },
    },
  },
  plugins: [],
};
