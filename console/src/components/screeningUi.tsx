// Presentation maps for image-screening results.
//
// Kept in a standalone module (no React state, no side effects) so the
// risk-band → style and flag-value → style mappings can be unit-tested
// directly. Every mapping pairs COLOUR with an ICON + TEXT, so meaning is
// never conveyed by colour alone (colour-blind / accessibility requirement).

import {
  ShieldAlert, AlertTriangle, Info, CheckCircle2, HelpCircle, MinusCircle,
} from "lucide-react";
import type { RiskBand, ScreenFlag } from "../lib/types";

export interface RiskUi {
  /** Tailwind classes for the risk badge (bg + border + text). */
  cls:  string;
  icon: React.ReactNode;
}

// risk_band → colour + icon. Mirrors the RISK_UI pattern in LiveCamera and the
// risk-* tokens in tailwind.config.js. "clear" = no finding (NOT a clearance).
export const SCREEN_RISK_UI: Record<RiskBand, RiskUi> = {
  high:   { cls: "bg-risk-high-bg border-risk-high-border text-risk-high-text",       icon: <ShieldAlert   size={14} aria-hidden="true" /> },
  medium: { cls: "bg-risk-medium-bg border-risk-medium-border text-risk-medium-text", icon: <AlertTriangle size={14} aria-hidden="true" /> },
  low:    { cls: "bg-risk-low-bg border-risk-low-border text-risk-low-text",          icon: <Info          size={14} aria-hidden="true" /> },
  clear:  { cls: "bg-risk-clear-bg border-risk-clear-border text-risk-clear-text",    icon: <CheckCircle2  size={14} aria-hidden="true" /> },
};

export interface FlagUi {
  /** Tailwind classes for the flag chip. */
  cls:  string;
  icon: React.ReactNode;
}

// flag value → colour + icon.
//   BOR      = present  → red  (danger)
//   SHUBHALI = suspected → amber (warning)
//   YO'Q     = not detected → neutral (NEVER green/"safe")
export const SCREEN_FLAG_UI: Record<ScreenFlag, FlagUi> = {
  "BOR":      { cls: "bg-risk-high-bg border-risk-high-border text-risk-high-text",       icon: <ShieldAlert   size={12} aria-hidden="true" /> },
  "SHUBHALI": { cls: "bg-risk-medium-bg border-risk-medium-border text-risk-medium-text", icon: <HelpCircle    size={12} aria-hidden="true" /> },
  "YO'Q":     { cls: "bg-surface-card border-surface-border text-content-muted",          icon: <MinusCircle   size={12} aria-hidden="true" /> },
};
