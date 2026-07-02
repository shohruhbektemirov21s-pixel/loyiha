import { ShieldAlert, AlertTriangle, Info, ShieldCheck, X } from "lucide-react";
import type { RiskBand } from "../lib/types";
import { BAND, hexA } from "../lib/theme";
import { RISK_BAND, SR_CLOSE } from "../lib/uz";

interface Props {
  risk:       RiskBand;
  sub?:       string;       // status line (e.g. "3 ta topilma aniqlandi")
  summaryUz?: string;       // fallback when no explicit sub is given
  onDismiss?: () => void;
}

// Distinct icon per band — never colour-only (color-blind safe).
const ICON: Record<RiskBand, React.ReactNode> = {
  high:   <ShieldAlert size={24} aria-hidden="true" />,
  medium: <AlertTriangle size={24} aria-hidden="true" />,
  low:    <Info size={24} aria-hidden="true" />,
  clear:  <ShieldCheck size={24} aria-hidden="true" />,
};

export function AlertBanner({ risk, sub, summaryUz, onDismiss }: Props) {
  const { color } = BAND[risk];
  const subText = sub ?? summaryUz;
  const muted = risk === "clear";

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={`flex items-center ${risk === "high" ? "halo-high" : ""}`}
      style={{
        gap: 14, padding: "16px 20px", borderRadius: 14,
        background: hexA(color, muted ? 0.08 : 0.12),
        border: `1px solid ${hexA(color, 0.42)}`,
        boxShadow: risk === "high" ? undefined : `0 8px 30px ${hexA(color, 0.12)}`,
      }}
    >
      <span
        className="grid place-items-center shrink-0"
        style={{ color, width: 42, height: 42, borderRadius: 11, background: hexA(color, 0.18) }}
      >
        {ICON[risk]}
      </span>

      <div className="flex-1 min-w-0">
        <div style={{ fontSize: 18, fontWeight: 700, letterSpacing: "-0.02em" }}>{RISK_BAND[risk]}</div>
        {subText && (
          <div style={{ fontSize: 13, color: "#cbd5e1", opacity: 0.9 }}>{subText}</div>
        )}
      </div>

      {onDismiss && (
        <button onClick={onDismiss} className="shrink-0" aria-label={SR_CLOSE}
          style={{ padding: 2, borderRadius: 6, color: "#aebbcf", background: "transparent", border: "none", cursor: "pointer" }}>
          <X size={16} />
        </button>
      )}
    </div>
  );
}
