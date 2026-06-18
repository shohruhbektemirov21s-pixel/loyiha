import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import type {
  Detection, DetectionVerdict, DetectionJudgement, ThreatCategory,
} from "../lib/types";
import { ConfidenceMeter } from "./ConfidenceMeter";
import {
  THREAT_CATEGORY, LOCATION_LABEL, SIZE_LABEL,
  DETECTION_ATTRIBUTES, VERDICT_CONFIDENCE, DETECTION_SCORE,
  JUDGE_CONFIRM, JUDGE_REJECT, JUDGE_RECLASSIFY,
  JUDGE_LABEL, MISSED_CATEGORY,
} from "../lib/uz";

// ------------------------------------------------------------------
// Colour per category
// ------------------------------------------------------------------
const CAT_COLOR: Record<ThreatCategory, { border: string; accent: string }> = {
  firearm:          { border: "border-red-700",    accent: "text-red-400" },
  explosive:        { border: "border-red-700",    accent: "text-red-400" },
  bladed_weapon:    { border: "border-orange-700", accent: "text-orange-400" },
  narcotics:        { border: "border-purple-700", accent: "text-purple-400" },
  currency:         { border: "border-yellow-700", accent: "text-yellow-400" },
  organic_anomaly:  { border: "border-cyan-800",   accent: "text-cyan-400" },
  metallic_anomaly: { border: "border-slate-600",  accent: "text-slate-400" },
  contraband_other: { border: "border-amber-700",  accent: "text-amber-400" },
  unknown:          { border: "border-slate-700",  accent: "text-slate-400" },
};

const CATEGORIES: ThreatCategory[] = [
  "firearm", "explosive", "bladed_weapon", "narcotics",
  "currency", "organic_anomaly", "metallic_anomaly", "contraband_other", "unknown",
];

// ------------------------------------------------------------------
interface Props {
  detection:      Detection;
  verdict?:       DetectionVerdict;
  judgement:      DetectionJudgement;
  corrected?:     ThreatCategory | null;
  selected:       boolean;
  onSelect:       () => void;
  onJudge:        (j: DetectionJudgement, corrected?: ThreatCategory) => void;
}

export function DetectionCard({
  detection, verdict, judgement, corrected, selected, onSelect, onJudge,
}: Props) {
  const [expanded, setExpanded]   = useState(false);
  const [reclass, setReclass]     = useState(false);
  const [newCat, setNewCat]       = useState<ThreatCategory>(detection.category);

  const cat    = corrected ?? detection.category;
  const colors = CAT_COLOR[cat];
  const isLow  = detection.score < 0.45;

  return (
    <div
      className={`rounded-lg border transition-all ${colors.border} ${
        selected
          ? "bg-surface-hover ring-1 ring-offset-0"
          : "bg-surface-card hover:bg-surface-hover"
      } ${isLow ? "opacity-75" : ""}`}
    >
      {/* Header — the single clickable select target (a real <button>, so no
          nested-interactive antipattern with the controls below) */}
      <div className="flex items-start gap-2 p-3">
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          aria-label={`${THREAT_CATEGORY[cat]} — ${Math.round(detection.score * 100)}%`}
          className="flex-1 min-w-0 text-left cursor-pointer rounded focus:outline-none focus-visible:ring-1 focus-visible:ring-blue-500"
        >
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-sm font-semibold ${colors.accent}`}>
              {THREAT_CATEGORY[cat]}
            </span>
            {judgement !== "unreviewed" && (
              <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                judgement === "confirmed"    ? "bg-green-900/50 text-green-400" :
                judgement === "rejected"     ? "bg-red-900/50 text-red-400" :
                "bg-amber-900/50 text-amber-400"
              }`}>
                {JUDGE_LABEL[judgement]}
              </span>
            )}
          </div>
          <p className="text-xs text-content-muted mt-0.5 font-mono">
            {detection.native_label}
          </p>
        </button>

        <button
          type="button"
          onClick={() => setExpanded((x) => !x)}
          className="shrink-0 p-1 text-content-muted hover:text-content-primary"
          aria-label={expanded ? "Yopish" : "Ko'proq"}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>

      {/* Confidence bars */}
      <div className="px-3 pb-2 space-y-2" onClick={(e) => e.stopPropagation()}>
        <ConfidenceMeter value={detection.score}    label={DETECTION_SCORE}  size="sm" showNote />
        {verdict && (
          <ConfidenceMeter value={verdict.confidence} label={VERDICT_CONFIDENCE} size="sm" />
        )}
      </div>

      {/* VLM rationale */}
      {verdict && (
        <div className="px-3 pb-2 text-xs text-content-secondary leading-relaxed">
          {verdict.rationale_uz}
        </div>
      )}

      {/* Expanded details */}
      {expanded && (
        <div className="px-3 pb-3 space-y-2 text-xs border-t border-surface-border pt-2 mt-1"
             onClick={(e) => e.stopPropagation()}>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1">
            <span className="text-content-muted">{LOCATION_LABEL}</span>
            <span className="text-content-secondary font-mono">
              {detection.box.x},{detection.box.y}
            </span>
            <span className="text-content-muted">{SIZE_LABEL}</span>
            <span className="text-content-secondary font-mono">
              {detection.box.width}×{detection.box.height}
            </span>
          </div>

          {Object.entries(detection.attributes).length > 0 && (
            <div>
              <p className="text-content-muted mb-1">{DETECTION_ATTRIBUTES}</p>
              <div className="flex flex-wrap gap-1">
                {Object.entries(detection.attributes).map(([k, v]) => (
                  <span key={k} className="px-1.5 py-0.5 rounded bg-surface-border text-content-secondary">
                    {k}: {v}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Judgement controls */}
      <div className="px-3 pb-3 flex flex-wrap gap-1.5" onClick={(e) => e.stopPropagation()}>
        <button
          className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
            judgement === "confirmed"
              ? "bg-green-700 text-white"
              : "bg-surface-border text-content-secondary hover:bg-green-900/40 hover:text-green-300"
          }`}
          onClick={() => onJudge("confirmed")}
        >
          {JUDGE_CONFIRM}
        </button>

        <button
          className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
            judgement === "rejected"
              ? "bg-red-800 text-white"
              : "bg-surface-border text-content-secondary hover:bg-red-900/40 hover:text-red-300"
          }`}
          onClick={() => onJudge("rejected")}
        >
          {JUDGE_REJECT}
        </button>

        <button
          className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
            reclass || judgement === "reclassified"
              ? "bg-amber-800 text-white"
              : "bg-surface-border text-content-secondary hover:bg-amber-900/40 hover:text-amber-300"
          }`}
          onClick={() => setReclass((r) => !r)}
        >
          {JUDGE_RECLASSIFY}
        </button>
      </div>

      {/* Reclassify picker */}
      {reclass && (
        <div className="px-3 pb-3 flex gap-2 items-center" onClick={(e) => e.stopPropagation()}>
          <label className="text-xs text-content-muted shrink-0">{MISSED_CATEGORY}</label>
          <select
            className="flex-1 bg-surface-card border border-surface-border rounded px-2 py-1 text-xs text-content-primary focus:outline-none focus:ring-1 focus:ring-amber-500"
            value={newCat}
            onChange={(e) => setNewCat(e.target.value as ThreatCategory)}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{THREAT_CATEGORY[c]}</option>
            ))}
          </select>
          <button
            className="px-2 py-1 rounded text-xs font-semibold bg-amber-600 hover:bg-amber-500 text-white"
            onClick={() => { onJudge("reclassified", newCat); setReclass(false); }}
          >
            OK
          </button>
        </div>
      )}
    </div>
  );
}
