/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface:  { DEFAULT: "#0f1117", card: "#1a1d27", border: "#2a2d3a", hover: "#22263a" },
        risk: {
          high:   { DEFAULT: "#ef4444", bg: "#3b0a0a", border: "#7f1d1d", text: "#fca5a5" },
          medium: { DEFAULT: "#f59e0b", bg: "#3b2000", border: "#92400e", text: "#fcd34d" },
          low:    { DEFAULT: "#3b82f6", bg: "#0f1f3b", border: "#1e3a8a", text: "#93c5fd" },
          clear:  { DEFAULT: "#22c55e", bg: "#052e16", border: "#14532d", text: "#86efac" },
        },
        conf: {
          high:   "#22c55e",
          medium: "#f59e0b",
          low:    "#ef4444",
        },
        content: {
          primary:   "#f1f5f9",
          secondary: "#94a3b8",
          muted:     "#475569",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      animation: {
        "pulse-fast": "pulse 0.8s cubic-bezier(0.4,0,0.6,1) infinite",
        "fade-in":    "fadeIn 0.15s ease-out",
        "slide-in":   "slideIn 0.2s ease-out",
      },
      keyframes: {
        fadeIn:  { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        slideIn: { "0%": { opacity: "0", transform: "translateY(-4px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
      },
    },
  },
  plugins: [],
};
