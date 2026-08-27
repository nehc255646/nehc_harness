/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: "var(--color-accent)",
        bg: "var(--color-bg)",
      },
    },
  },
  plugins: [],
};
