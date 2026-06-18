import { ShieldAlert, AlertTriangle, Info, CheckCircle2, X } from "lucide-react";
import type { RiskBand } from "../lib/types";
import { RISK_BAND, CLEAR_DISCLAIMER, SR_CLOSE } from "../lib/uz";

interface Props {
  risk:       RiskBand;
  summaryUz?: string;
  onDismiss?: () => void;
}

const CONFIG: Record<RiskBand, {
  icon:  React.ReactNode;
  outer: string;
  inner: string;
  title: string;
}> = {
  high: {
    // Distinct shield icon — high is visually different from medium, not
    // colour-only (color-blind safe).
    icon:  <ShieldAlert size={20} aria-hidden="true" />,
    outer: "border-risk-high-border bg-risk-high-bg",
    inner: "text-risk-high-text",
    title: RISK_BAND.high,
  },
  medium: {
    icon:  <AlertTriangle size={20} aria-hidden="true" />,
    outer: "border-risk-medium-border bg-risk-medium-bg",
    inner: "text-risk-medium-text",
    title: RISK_BAND.medium,
  },
  low: {
    icon:  <Info size={20} aria-hidden="true" />,
    outer: "border-risk-low-border bg-risk-low-bg",
    inner: "text-risk-low-text",
    title: RISK_BAND.low,
  },
  clear: {
    icon:  <CheckCircle2 size={20} aria-hidden="true" />,
    outer: "border-risk-clear-border bg-risk-clear-bg",
    inner: "text-risk-clear-text",
    title: RISK_BAND.clear,
  },
};

export function AlertBanner({ risk, summaryUz, onDismiss }: Props) {
  const cfg = CONFIG[risk];

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={`relative flex gap-3 items-start p-3 rounded-lg border animate-slide-in ${cfg.outer}`}
    >
      <span className={cfg.inner}>{cfg.icon}</span>

      <div className="flex-1 min-w-0">
        <p className={`text-sm font-semibold ${cfg.inner}`}>{cfg.title}</p>
        {summaryUz && (
          <p className="mt-1 text-sm text-content-secondary leading-relaxed">
            {summaryUz}
          </p>
        )}
        {risk === "clear" && (
          <p className="mt-1 text-sm text-content-muted leading-relaxed italic">
            {CLEAR_DISCLAIMER}
          </p>
        )}
      </div>

      {onDismiss && (
        <button
          onClick={onDismiss}
          className="shrink-0 p-0.5 rounded text-content-muted hover:text-content-primary transition-colors"
          aria-label={SR_CLOSE}
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}
