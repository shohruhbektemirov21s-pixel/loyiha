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
  const detections: Detection[] = det?.detections ?? [];
  const analyzing = scan.state === "analyzing";

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
    <div className="flex flex-col h-full gap-4 overflow-hidden">

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
                  className={`px-2 py-0.5 rounded text-xs transition-colors ${
                    i === frameIdx
                      ? "bg-blue-700 text-white"
                      : "bg-surface-border text-content-secondary hover:bg-surface-hover"
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
            <h3 id="det-heading" className="text-xs font-semibold text-content-secondary uppercase tracking-wide mb-2">
              {DETECTIONS_TITLE}
              {detections.length > 0 && (
                <span className="ml-1.5 text-content-muted font-normal normal-case">
                  ({detections.length})
                </span>
              )}
            </h3>

            {detections.length === 0 && (
              <p className="text-sm text-content-muted">{NO_DETECTIONS}</p>
            )}

            <div className="space-y-2">
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
            <section aria-labelledby="verd-heading" className="rounded-lg border border-surface-border bg-surface-card p-3 space-y-2">
              <h3 id="verd-heading" className="text-xs font-semibold text-content-secondary uppercase tracking-wide">
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
            <h3 id="missed-heading" className="text-xs font-semibold text-content-secondary uppercase tracking-wide mb-2">
              {MISSED_TITLE}
              {annotations.length > 0 && (
                <span className="ml-1.5 text-content-muted font-normal normal-case">
                  ({annotations.length})
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
