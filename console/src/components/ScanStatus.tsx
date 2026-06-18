import type { ScanState, RiskBand } from "../lib/types";
import { SCAN_STATE, RISK_BAND_SHORT } from "../lib/uz";

const STATE_STYLE: Record<ScanState, string> = {
  pending:   "bg-slate-700 text-slate-300",
  analyzing: "bg-blue-900/60 text-blue-300 animate-pulse",
  analyzed:  "bg-blue-800/60 text-blue-200",
  verdicted: "bg-amber-900/60 text-amber-200",
  reviewing: "bg-amber-800/60 text-amber-100",
  decided:   "bg-slate-700 text-slate-300",
  error:     "bg-red-900/60 text-red-300",
};

const RISK_STYLE: Record<RiskBand, string> = {
  clear:  "bg-risk-clear-bg border border-risk-clear-border text-risk-clear-text",
  low:    "bg-risk-low-bg  border border-risk-low-border  text-risk-low-text",
  medium: "bg-risk-medium-bg border border-risk-medium-border text-risk-medium-text",
  high:   "bg-risk-high-bg border border-risk-high-border text-risk-high-text",
};

interface Props {
  state:       ScanState;
  risk?:       RiskBand | null;
  showLabel?:  boolean;
}

export function ScanStatus({ state, risk, showLabel = true }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${STATE_STYLE[state]}`}>
        {state === "analyzing" && (
          <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse-fast" aria-hidden="true" />
        )}
        {showLabel && SCAN_STATE[state]}
      </span>

      {risk && (
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${RISK_STYLE[risk]}`}>
          {RISK_BAND_SHORT[risk]}
        </span>
      )}
    </div>
  );
}
