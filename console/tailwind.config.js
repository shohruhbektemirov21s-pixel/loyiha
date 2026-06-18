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
        // ── Neutral SECTION ACCENTS ──────────────────────────────────────
        // Per-section identity colours. PURELY decorative (section headers,
        // accent bars, icon tiles, tabs). Deliberately chosen so they NEVER
        // collide with the risk palette (red/amber/blue/green stay reserved for
        // risk semantics ONLY). Each main section gets its own hue so the
        // console reads as distinct zones, not one monotone surface.
        //   queue   → steel slate   (the inventory rail)
        //   live    → teal          (motion / realtime)
        //   screen  → violet        (AI vision / upload)
        //   decide  → indigo        (operator authority / command)
        accent: {
          queue:  "#94a3b8",  // steel
          live:   "#2dd4bf",  // teal
          screen: "#a78bfa",  // violet
          decide: "#818cf8",  // indigo
        },
        content: {
          primary:   "#f1f5f9",
          secondary: "#aab4c2",  // ~7:1 on #0f1117
          muted:     "#8b97a8",  // ~4.6:1 on #0f1117 — WCAG AA (was #475569, ~3:1, failing)
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      // ── Depth / elevation tokens ─────────────────────────────────────
      // Layered, soft shadows establish a clear z-hierarchy (sunken → raised →
      // floating → modal). Each pairs a tight ambient shadow with a diffuse
      // directional one for a realistic "lifted" feel on dark glass.
      boxShadow: {
        "elev-1":  "0 1px 2px rgba(0,0,0,0.40), 0 1px 1px rgba(0,0,0,0.30)",
        "elev-2":  "0 2px 4px rgba(0,0,0,0.45), 0 4px 10px rgba(0,0,0,0.35)",
        "elev-3":  "0 6px 14px rgba(0,0,0,0.50), 0 10px 28px rgba(0,0,0,0.40)",
        "elev-4":  "0 12px 28px rgba(0,0,0,0.55), 0 22px 50px rgba(0,0,0,0.45)",
        "inset-1": "inset 0 1px 2px rgba(0,0,0,0.55), inset 0 0 0 1px rgba(255,255,255,0.02)",
        "inset-2": "inset 0 2px 8px rgba(0,0,0,0.70), inset 0 0 0 1px rgba(255,255,255,0.03)",
        "rim":     "inset 0 1px 0 0 rgba(255,255,255,0.06)",
        // Risk glows — depth cue for severity (red strongest / most salient).
        "glow-high":   "0 0 0 1px rgba(239,68,68,0.55), 0 0 22px rgba(239,68,68,0.45), 0 0 48px rgba(239,68,68,0.25)",
        "glow-medium": "0 0 0 1px rgba(245,158,11,0.45), 0 0 16px rgba(245,158,11,0.30)",
        "glow-low":    "0 0 0 1px rgba(59,130,246,0.40), 0 0 14px rgba(59,130,246,0.25)",
        "glow-blue":   "0 0 0 1px rgba(59,130,246,0.50), 0 0 18px rgba(59,130,246,0.35)",
        // Neutral accent glows — soft, decorative section identity (NOT risk).
        "glow-teal":    "0 0 0 1px rgba(45,212,191,0.35), 0 0 16px rgba(45,212,191,0.22)",
        "glow-violet":  "0 0 0 1px rgba(167,139,250,0.35), 0 0 16px rgba(167,139,250,0.22)",
        "glow-indigo":  "0 0 0 1px rgba(129,140,248,0.38), 0 0 16px rgba(129,140,248,0.24)",
        "glow-steel":   "0 0 0 1px rgba(148,163,184,0.30), 0 0 14px rgba(148,163,184,0.16)",
      },
      backdropBlur: {
        glass: "14px",
        xs:    "4px",
      },
      backgroundImage: {
        "depth-app":    "radial-gradient(1200px 600px at 20% -10%, rgba(59,130,246,0.10), transparent 60%), radial-gradient(900px 500px at 110% 10%, rgba(124,58,237,0.08), transparent 55%), linear-gradient(180deg, #0f1117 0%, #0c0e14 100%)",
        "depth-card":   "linear-gradient(160deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.015) 24%, rgba(0,0,0,0.10) 100%)",
        "depth-raised": "linear-gradient(160deg, rgba(255,255,255,0.07) 0%, rgba(255,255,255,0.02) 30%, rgba(0,0,0,0.12) 100%)",
        "glass-sheen":  "linear-gradient(120deg, rgba(255,255,255,0.10) 0%, rgba(255,255,255,0) 40%)",
        "grid-fine":    "linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px)",
      },
      backgroundSize: {
        "grid-fine": "32px 32px",
      },
      borderRadius: {
        "xl2": "1rem",
      },
      animation: {
        "pulse-fast": "pulse 0.8s cubic-bezier(0.4,0,0.6,1) infinite",
        "fade-in":    "fadeIn 0.18s ease-out",
        "slide-in":   "slideIn 0.22s cubic-bezier(0.16,1,0.3,1)",
        "rise-in":    "riseIn 0.30s cubic-bezier(0.16,1,0.3,1)",
        "halo":       "halo 1.8s cubic-bezier(0.4,0,0.6,1) infinite",
        "shimmer":    "shimmer 1.6s ease-in-out infinite",
        "scan-sweep": "scanSweep 2.4s cubic-bezier(0.4,0,0.6,1) infinite",
      },
      keyframes: {
        fadeIn:  { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        slideIn: { "0%": { opacity: "0", transform: "translateY(-4px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
        riseIn:  { "0%": { opacity: "0", transform: "translateY(10px) scale(0.985)" }, "100%": { opacity: "1", transform: "translateY(0) scale(1)" } },
        halo: {
          "0%, 100%": { boxShadow: "0 0 0 1px rgba(239,68,68,0.55), 0 0 18px rgba(239,68,68,0.40), 0 0 40px rgba(239,68,68,0.18)" },
          "50%":      { boxShadow: "0 0 0 1px rgba(239,68,68,0.75), 0 0 30px rgba(239,68,68,0.65), 0 0 70px rgba(239,68,68,0.35)" },
        },
        shimmer: {
          "0%":   { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        scanSweep: {
          "0%":        { transform: "translateY(-100%)", opacity: "0" },
          "10%, 90%":  { opacity: "1" },
          "100%":      { transform: "translateY(900%)", opacity: "0" },
        },
      },
    },
  },
  plugins: [],
};
