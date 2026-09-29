/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          base: "#0b0c0e",
          raised: "#111317",
          panel: "#15171c",
          hover: "#1c1f26",
        },
        border: { subtle: "#23262d", strong: "#2e323b" },
        fg: { DEFAULT: "#e6e8eb", muted: "#9aa1ac", faint: "#6b7280" },
        accent: { DEFAULT: "#6366f1", hover: "#7c7ff2", subtle: "#6366f11a" },
        danger: "#ef4444",
        warn: "#f59e0b",
        ok: "#22c55e",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.12s ease-out",
        "slide-up": "slide-up 0.14s ease-out",
      },
    },
  },
  plugins: [],
};
