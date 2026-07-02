import { useState } from "react";
import type {
  Detection, DetectionVerdict, DetectionJudgement, ThreatCategory,
} from "../lib/types";
import { catColor, hexA } from "../lib/theme";
import {
  THREAT_CATEGORY, JUDGE_CONFIRM, JUDGE_REJECT,
  JUDGE_LABEL,
} from "../lib/uz";

const CATEGORIES: ThreatCategory[] = [
  "firearm", "explosive", "bladed_weapon", "narcotics",
  "currency", "organic_anomaly", "metallic_anomaly", "contraband_other", "unknown",
];

// Judgement → badge label + colour.
const JUDGE_BADGE: Record<Exclude<DetectionJudgement, "unreviewed">, { label: string; color: string }> = {
  confirmed:    { label: JUDGE_LABEL.confirmed,    color: "#22c55e" },
  rejected:     { label: JUDGE_LABEL.rejected,     color: "#ef4444" },
  reclassified: { label: JUDGE_LABEL.reclassified, color: "#f59e0b" },
};

interface Props {
  detection:  Detection;
  verdict?:   DetectionVerdict;
  judgement:  DetectionJudgement;
  corrected?: ThreatCategory | null;
  selected:   boolean;
  onSelect:   () => void;
  onJudge:    (j: DetectionJudgement, corrected?: ThreatCategory) => void;
}

export function DetectionCard({
  detection, verdict, judgement, corrected, selected, onSelect, onJudge,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [reclass, setReclass]   = useState(false);
  const [newCat, setNewCat]     = useState<ThreatCategory>(detection.category);

  const cat   = corrected ?? detection.category;
  const color = catColor(cat);
  const isLow = detection.score < 0.45;

  const detPct = Math.round(detection.score * 100);
  const vlmPct = verdict ? Math.round(verdict.confidence * 100) : null;

  const attrs   = detection.attributes ?? {};
  const material = attrs.material ?? attrs.mean_density ?? "—";
  const density  = attrs.density ?? attrs.mean_density ?? "—";
  const pixel    = `x:${detection.box.x} y:${detection.box.y} w:${detection.box.width} h:${detection.box.height}`;

  const badge = judgement !== "unreviewed" ? JUDGE_BADGE[judgement] : null;

  const judgeBtn = (active: boolean, col: string): React.CSSProperties => ({
    flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 5,
    padding: "7px 4px", fontSize: 11.5, fontWeight: 600, borderRadius: 8, cursor: "pointer",
    border: `1px solid ${active ? col : "rgba(255,255,255,0.12)"}`,
    background: active ? hexA(col, 0.16) : "rgba(255,255,255,0.03)",
    color: active ? col : "#aebbcf", transition: "all .12s",
  });

  return (
    <div
      onClick={onSelect}
      role="button"
      aria-pressed={selected}
      aria-label={`${THREAT_CATEGORY[cat]} — ${detPct}%`}
      style={{
        background: "rgba(255,255,255,0.035)",
        borderStyle: "solid",
        borderWidth: "1px 1px 1px 3px",
        borderColor: (() => {
          const c = selected ? "rgba(129,140,248,0.55)" : "rgba(255,255,255,0.08)";
          return `${c} ${c} ${c} ${color}`;
        })(),
        borderRadius: 12, padding: "11px 12px", opacity: isLow ? 0.74 : 1,
        boxShadow: selected ? "0 0 0 1px rgba(129,140,248,0.3),0 8px 22px rgba(99,102,241,0.14)" : undefined,
        transition: "all .15s", cursor: "pointer",
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between" style={{ gap: 8 }}>
        <div className="flex items-center min-w-0" style={{ gap: 8 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: color, flex: "none" }} aria-hidden="true" />
          <span style={{ fontSize: 13, fontWeight: 600, color }}>{THREAT_CATEGORY[cat]}</span>
          {isLow && (
            <span style={{ fontSize: 9, fontWeight: 600, padding: "1px 6px", borderRadius: 999, color: "#94a3b8", background: "rgba(148,163,184,0.12)" }}>
              past ishonch
            </span>
          )}
        </div>
        <div className="flex items-center" style={{ gap: 7, flex: "none" }}>
          {badge && (
            <span style={{
              fontSize: 10.5, fontWeight: 600, padding: "2px 7px", borderRadius: 999,
              color: badge.color, background: hexA(badge.color, 0.15), border: `1px solid ${hexA(badge.color, 0.4)}`,
            }}>{badge.label}</span>
          )}
          <span
            role="button"
            aria-expanded={expanded}
            aria-label={expanded ? "Yopish" : "Ko'proq"}
            onClick={(e) => { e.stopPropagation(); setExpanded((x) => !x); }}
            style={{ fontSize: 13, color: "#7c8aa3", cursor: "pointer", width: 16, textAlign: "center" }}
          >
            {expanded ? "▾" : "▸"}
          </span>
        </div>
      </div>

      {/* Native label */}
      <div className="font-mono" style={{ fontSize: 10.5, color: "#5b6679", margin: "6px 0 10px" }}>
        {detection.native_label}
      </div>

      {/* Confidence bars */}
      <div className="flex flex-col" style={{ gap: 7, marginBottom: 9 }}>
        <Bar label="Detektor" pct={detPct} color={color} />
        <Bar label="VLM ishonchi" pct={vlmPct} color="#818cf8" />
      </div>

      {/* VLM rationale */}
      {verdict && (
        <div style={{ fontSize: 12, fontStyle: "italic", color: "#94a3b8", lineHeight: 1.5 }}>
          {verdict.rationale_uz}
        </div>
      )}

      {/* Expanded details */}
      {expanded && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8,
            paddingTop: 10, borderTop: "1px solid rgba(255,255,255,0.08)",
          }}
        >
          <div style={{ gridColumn: "1 / -1" }}>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "#6b7a93" }}>Piksel sohasi</div>
            <div className="font-mono" style={{ fontSize: 11.5, color: "#aebbcf" }}>{pixel}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "#6b7a93" }}>Material</div>
            <div style={{ fontSize: 12, color: "#cbd5e1" }}>{material}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "#6b7a93" }}>Zichlik</div>
            <div className="font-mono" style={{ fontSize: 12, color: "#cbd5e1" }}>{density}</div>
          </div>
        </div>
      )}

      {/* Judgement controls */}
      <div className="flex" style={{ gap: 6, marginTop: 11 }} onClick={(e) => e.stopPropagation()}>
        <div style={judgeBtn(judgement === "confirmed", "#22c55e")} onClick={() => onJudge("confirmed")}>{JUDGE_CONFIRM}</div>
        <div style={judgeBtn(judgement === "rejected", "#ef4444")} onClick={() => onJudge("rejected")}>{JUDGE_REJECT}</div>
        <div style={judgeBtn(reclass || judgement === "reclassified", "#f59e0b")} onClick={() => setReclass((r) => !r)}>Qayta</div>
      </div>

      {/* Reclassify picker */}
      {reclass && (
        <select
          value={newCat}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => { const c = e.target.value as ThreatCategory; setNewCat(c); onJudge("reclassified", c); }}
          style={{
            width: "100%", marginTop: 8, padding: "7px 9px", borderRadius: 8,
            background: "rgba(0,0,0,0.3)", border: "1px solid rgba(245,158,11,0.4)", color: "#e2e8f0", fontSize: 12.5,
          }}
        >
          {CATEGORIES.map((c) => <option key={c} value={c}>{THREAT_CATEGORY[c]}</option>)}
        </select>
      )}
    </div>
  );
}

// Single labelled confidence bar.
function Bar({ label, pct, color }: { label: string; pct: number | null; color: string }) {
  return (
    <div>
      <div className="flex justify-between" style={{ fontSize: 10.5, color: "#7c8aa3", marginBottom: 3 }}>
        <span>{label}</span>
        <span className="font-mono" style={{ color: "#cbd5e1" }}>{pct === null ? "—" : `${pct}%`}</span>
      </div>
      <div style={{ height: 5, borderRadius: 999, background: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct ?? 0}%`, background: color, borderRadius: 999 }} />
      </div>
    </div>
  );
}
