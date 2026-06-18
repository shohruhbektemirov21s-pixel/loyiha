import type { UnitInterval } from "../lib/types";
import { CONF_HIGH_LABEL, CONF_MEDIUM_LABEL, CONF_LOW_LABEL, CONF_NOTE_LOW } from "../lib/uz";

interface Props {
  value: UnitInterval;
  label?: string;
  showNote?: boolean;
  size?: "sm" | "md";
}

function band(v: number): { color: string; bg: string; label: string } {
  if (v >= 0.75) return { color: "#22c55e", bg: "rgba(34,197,94,0.15)",  label: CONF_HIGH_LABEL };
  if (v >= 0.45) return { color: "#f59e0b", bg: "rgba(245,158,11,0.15)", label: CONF_MEDIUM_LABEL };
  return             { color: "#ef4444", bg: "rgba(239,68,68,0.15)",    label: CONF_LOW_LABEL };
}

export function ConfidenceMeter({ value, label, showNote, size = "md" }: Props) {
  const pct  = Math.round(value * 100);
  const info = band(value);
  const h    = size === "sm" ? "h-1.5" : "h-2";

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        {label && (
          <span className="text-xs text-slate-400">{label}</span>
        )}
        <span
          className="ml-auto text-xs font-mono font-semibold tabular-nums"
          style={{ color: info.color }}
          aria-label={`${pct}% — ${info.label}`}
        >
          {pct}%
        </span>
      </div>

      {/* Track */}
      <div
        className={`w-full rounded-full ${h} bg-surface-border overflow-hidden surface-sunken`}
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={info.label}
      >
        <div
          className={`${h} rounded-full transition-all duration-300`}
          style={{ width: `${pct}%`, backgroundColor: info.color }}
        />
      </div>

      {/* Badge */}
      <span
        className="inline-block px-1.5 py-0.5 rounded text-xs font-medium"
        style={{ color: info.color, backgroundColor: info.bg }}
      >
        {info.label}
      </span>

      {showNote && value < 0.45 && (
        <p className="text-xs text-risk-low-text leading-snug mt-1">
          {CONF_NOTE_LOW}
        </p>
      )}
    </div>
  );
}
