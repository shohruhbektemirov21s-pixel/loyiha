import { useState } from "react";
import type {
  ScanRecord, Detection, DetectionJudgement, ThreatCategory,
  OperatorAnnotation,
} from "../lib/types";
import { AlertBanner } from "./AlertBanner";
import { DetectionCard } from "./DetectionCard";
import { XRayViewer } from "./XRayViewer";
import {
  DETECTIONS_TITLE, NO_DETECTIONS,
  VERDICT_TITLE, VERDICT_SUMMARY_LABEL, VERDICT_ADVISORY_NOTE,
  VERDICT_PENDING, VERDICT_UNAVAILABLE,
  MISSED_TITLE, MISSED_ADD, MISSED_DELETE,
  THREAT_CATEGORY, IMAGE_MODALITY, SCAN_SUBJECT,
} from "../lib/uz";

// Category danger ranking — most dangerous first when sorting detections.
const CATEGORY_SEVERITY: Record<ThreatCategory, number> = {
  explosive:        100,
  firearm:           90,
  bladed_weapon:     80,
  narcotics:         70,
  contraband_other:  50,
  currency:          40,
  metallic_anomaly:  30,
  organic_anomaly:   20,
  unknown:           10,
};

// ------------------------------------------------------------------
// Per-detection local judgement state
// ------------------------------------------------------------------
interface JudgementEntry {
  judgement:  DetectionJudgement;
  corrected:  ThreatCategory | null;
}

interface Props {
  scan:               ScanRecord;
  onJudgementsChange: (j: Record<string, JudgementEntry>) => void;
  onAnnotationsChange:(a: OperatorAnnotation[]) => void;
  judgements:         Record<string, JudgementEntry>;
  annotations:        OperatorAnnotation[];
}

export function VerdictPanel({
  scan, onJudgementsChange, onAnnotationsChange, judgements, annotations,
}: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [frameIdx,   setFrameIdx]   = useState(0);

  const det    = scan.detection;
  const verd   = scan.verdict;
  const frames = det?.frames ?? [];
  const frame  = frames[frameIdx] ?? null;
  const analyzing = scan.state === "analyzing";

  // Show the most dangerous detections first: by category severity, then by
  // detector score. (The raw AI order is not risk-prioritised.)
  const detections: Detection[] = [...(det?.detections ?? [])].sort((a, b) => {
    const sevDiff = CATEGORY_SEVERITY[b.category] - CATEGORY_SEVERITY[a.category];
    if (sevDiff !== 0) return sevDiff;
    return b.score - a.score;
  });

  // Map detections → verdict rationale
  const verdictMap = Object.fromEntries(
    (verd?.per_detection ?? []).map((dv) => [dv.detection_id, dv]),
  );

  const handleJudge = (
    detId: string,
    j: DetectionJudgement,
    corrected?: ThreatCategory,
  ) => {
    onJudgementsChange({
      ...judgements,
      [detId]: { judgement: j, corrected: corrected ?? null },
    });
  };

  const handleAddAnnotation = (
    a: Omit<OperatorAnnotation, "note_uz"> & { note_uz: string | null },
  ) => {
    onAnnotationsChange([...annotations, a as OperatorAnnotation]);
  };

  const removeAnnotation = (idx: number) => {
    onAnnotationsChange(annotations.filter((_, i) => i !== idx));
  };

  return (
    <div className="section-verdict flex flex-col h-full gap-4 overflow-hidden">

      {/* ── Risk banner ── */}
      {scan.overall_risk && (
        <AlertBanner
          risk={scan.overall_risk}
          summaryUz={verd?.summary_uz}
        />
      )}

      {/* ── Metadata strip ── */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-content-muted">
        <span>{SCAN_SUBJECT[scan.subject]}</span>
        <span>{IMAGE_MODALITY[scan.modality]}</span>
        {scan.lane_id && <span>{scan.lane_id}</span>}
        <span className="font-mono truncate max-w-[160px]" title={scan.scan_id}>
          {scan.scan_id.slice(0, 8)}…
        </span>
      </div>

      {/* ── Two-column: viewer + right panel ── */}
      <div className="flex gap-4 flex-1 min-h-0 overflow-hidden">

        {/* Left: viewer */}
        <div className="flex-1 min-w-0 flex flex-col min-h-0">
          {/* Frame tabs */}
          {frames.length > 1 && (
            <div className="flex gap-1 mb-2">
              {frames.map((f, i) => (
                <button
                  key={f.frame_id}
                  onClick={() => setFrameIdx(i)}
                  className={`press px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                    i === frameIdx
                      ? "bg-sky-500/20 text-sky-200 border border-sky-500/50 shadow-glow-low"
                      : "glass text-content-secondary hover:bg-surface-hover"
                  }`}
                >
                  {f.view_label ?? `Kadr ${i + 1}`}
                </button>
              ))}
            </div>
          )}

          <XRayViewer
            scanId={scan.scan_id}
            frame={frame}
            detections={detections.filter((d) => d.frame_id === frame?.frame_id)}
            selectedId={selectedId}
            onSelect={setSelectedId}
            analyzing={analyzing}
            onAddAnnotation={handleAddAnnotation}
          />
        </div>

        {/* Right: detections + verdict */}
        <div className="w-72 shrink-0 flex flex-col gap-3 overflow-y-auto pr-0.5">

          {/* ── Detections ── */}
          <section aria-labelledby="det-heading">
            <h3 id="det-heading" className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider section-eyebrow mb-2">
              <span className="w-1 h-3 rounded-full bg-[var(--sec-accent)] opacity-80" aria-hidden="true" />
              {DETECTIONS_TITLE}
              {detections.length > 0 && (
                <span className="ml-0.5 px-1.5 py-px rounded-full bg-white/5 text-content-muted font-semibold normal-case tracking-normal">
                  {detections.length}
                </span>
              )}
            </h3>

            {detections.length === 0 && (
              <p className="text-sm text-content-muted">{NO_DETECTIONS}</p>
            )}

            <div className="space-y-2 scene">
              {detections.map((d) => (
                <DetectionCard
                  key={d.detection_id}
                  detection={d}
                  verdict={verdictMap[d.detection_id]}
                  judgement={judgements[d.detection_id]?.judgement ?? "unreviewed"}
                  corrected={judgements[d.detection_id]?.corrected}
                  selected={selectedId === d.detection_id}
                  onSelect={() =>
                    setSelectedId((s) => s === d.detection_id ? null : d.detection_id)
                  }
                  onJudge={(j, c) => handleJudge(d.detection_id, j, c)}
                />
              ))}
            </div>
          </section>

          {/* ── VLM summary ── */}
          {(verd || scan.state === "verdicted" || scan.state === "reviewing") && (
            <section aria-labelledby="verd-heading" className="rounded-xl border border-white/10 glass section-tint p-3 space-y-2">
              <h3 id="verd-heading" className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider section-eyebrow">
                <span className="w-1 h-3 rounded-full bg-[var(--sec-accent)] opacity-80" aria-hidden="true" />
                {VERDICT_TITLE}
              </h3>

              {!verd && (
                <p className="text-xs text-content-muted animate-pulse">{VERDICT_PENDING}</p>
              )}

              {verd && (
                <>
                  <p className="text-xs text-content-secondary mb-1">{VERDICT_SUMMARY_LABEL}</p>
                  <p className="text-sm text-content-primary leading-relaxed">{verd.summary_uz}</p>
                  <p className="text-xs text-content-muted italic mt-2">{VERDICT_ADVISORY_NOTE}</p>
                  {verd.model && (
                    <p className="text-xs text-content-muted font-mono mt-1">
                      {verd.model.name} {verd.model.version}
                    </p>
                  )}
                </>
              )}

              {scan.state === "analyzed" && !verd && (
                <p className="text-xs text-content-muted">{VERDICT_UNAVAILABLE}</p>
              )}
            </section>
          )}

          {/* ── Missed annotations ── */}
          <section aria-labelledby="missed-heading">
            <h3 id="missed-heading" className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider section-eyebrow mb-2">
              <span className="w-1 h-3 rounded-full bg-[var(--sec-accent)] opacity-80" aria-hidden="true" />
              {MISSED_TITLE}
              {annotations.length > 0 && (
                <span className="ml-0.5 px-1.5 py-px rounded-full bg-white/5 text-content-muted font-semibold normal-case tracking-normal">
                  {annotations.length}
                </span>
              )}
            </h3>

            {annotations.length === 0 && (
              <p className="text-xs text-content-muted">{MISSED_ADD} — rasmda belgilash rejimini yoqing.</p>
            )}

            <div className="space-y-1.5">
              {annotations.map((a, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 p-2 rounded bg-amber-900/20 border border-amber-800/40 text-xs"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-amber-300 font-medium">{THREAT_CATEGORY[a.category]}</p>
                    <p className="text-content-muted font-mono">
                      {a.box.x},{a.box.y} · {a.box.width}×{a.box.height}
                    </p>
                    {a.note_uz && <p className="text-content-secondary mt-0.5">{a.note_uz}</p>}
                  </div>
                  <button
                    onClick={() => removeAnnotation(i)}
                    className="text-content-muted hover:text-red-400 transition-colors shrink-0"
                    aria-label={MISSED_DELETE}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
