// Visual design tokens for the operator console — the single source of truth
// for threat-category and risk-band colour language. Mirrors the approved
// Claude Design prototype ("Rentgen nazorat tizimi.dc.html") so every panel
// speaks the same colour semantics.
//
// Risk-colour rule: red/amber stay reserved for risk. "clear" is NEUTRAL grey
// (NOT green) — absence of a finding is never styled as "safe to pass".

import type { ThreatCategory, RiskBand } from "./types";

// rgba() helper — turn a #rrggbb token into a translucent fill/border.
export function hexA(hex: string, a: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}

// ── Threat categories → colour + severity rank (higher = more salient) ──
export interface CatToken { color: string; sev: number; }

export const CAT: Record<ThreatCategory, CatToken> = {
  firearm:          { color: "#f87171", sev: 5 },
  explosive:        { color: "#f87171", sev: 5 },
  bladed_weapon:    { color: "#fb923c", sev: 4 },
  narcotics:        { color: "#c084fc", sev: 4 },
  currency:         { color: "#facc15", sev: 2 },
  organic_anomaly:  { color: "#22d3ee", sev: 2 },
  metallic_anomaly: { color: "#94a3b8", sev: 1 },
  contraband_other: { color: "#9ca3af", sev: 1 },
  unknown:          { color: "#9ca3af", sev: 1 },
};

export const catColor = (c: ThreatCategory): string => (CAT[c] ?? CAT.unknown).color;
export const catSev   = (c: ThreatCategory): number => (CAT[c] ?? CAT.unknown).sev;

// ── Risk bands → colour + translucent backdrop ──
// `clear` is neutral grey on purpose (see rule above).
export interface BandToken { color: string; bg: string; }

export const BAND: Record<RiskBand, BandToken> = {
  high:   { color: "#ef4444", bg: "rgba(239,68,68,0.14)" },
  medium: { color: "#f59e0b", bg: "rgba(245,158,11,0.14)" },
  low:    { color: "#38bdf8", bg: "rgba(56,189,248,0.14)" },
  clear:  { color: "#94a3b8", bg: "rgba(148,163,184,0.10)" },
};

// Fail-safe pseudo-bands used while a scan is mid-flight or errored.
export const BAND_ANALYZING: BandToken = { color: "#94a3b8", bg: "rgba(148,163,184,0.10)" };
export const BAND_FAILED:    BandToken = { color: "#f59e0b", bg: "rgba(245,158,11,0.14)" };

export const bandColor = (b: RiskBand | null | undefined): string =>
  b ? BAND[b].color : BAND_ANALYZING.color;
export const bandBg = (b: RiskBand | null | undefined): string =>
  b ? BAND[b].bg : BAND_ANALYZING.bg;

// ── Operator-outcome accent colours (decision panel + audit) ──
export const OUTCOME_COLOR = {
  inspected: "#3b82f6",
  cleared:   "#22c55e",
  seized:    "#ef4444",
  escalated: "#f59e0b",
} as const;

// ── Screening flag colours (BOR / SHUBHALI / YO'Q) ──
// YO'Q is neutral steel — "not detected" is never a reassuring green.
export const FLAG_COLOR: Record<string, string> = {
  "BOR":      "#f87171",
  "SHUBHALI": "#f59e0b",
  "YO'Q":     "#64748b",
};

// ── Shared accents ──
export const ACCENT = {
  teal:   "#14b8a6",
  teal2:  "#2dd4bf",
  indigo: "#6366f1",
  indigo2:"#818cf8",
  ok:     "#22c55e",
  danger: "#ef4444",
} as const;
