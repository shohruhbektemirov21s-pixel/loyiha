import { useState } from "react";
import type {
  ScanRecord, Detection, DetectionJudgement, ThreatCategory,
  OperatorAnnotation,
} from "../lib/types";
import { AlertBanner } from "./AlertBanner";
import { DetectionCard } from "./DetectionCard";
import { XRayViewer } from "./XRayViewer";
import { catColor } from "../lib/theme";
import {
  DETECTIONS_TITLE,
  VERDICT_TITLE, VERDICT_ADVISORY_NOTE, VERDICT_UNAVAILABLE,
  MISSED_TITLE, MISSED_DELETE,
  THREAT_CATEGORY, IMAGE_MODALITY, SCAN_SUBJECT,
} from "../lib/uz";

// Most dangerous detections first: by category severity, then detector score.
const CATEGORY_SEVERITY: Record<ThreatCategory, number> = {
  explosive: 100, firearm: 90, bladed_weapon: 80, narcotics: 70,
  contraband_other: 50, currency: 40, metallic_anomaly: 30, organic_anomaly: 20, unknown: 10,
};

interface JudgementEntry {
  judgement: DetectionJudgement;
  corrected: ThreatCategory | null;
}

interface Props {
  scan:                ScanRecord;
  onJudgementsChange:  (j: Record<string, JudgementEntry>) => void;
  onAnnotationsChange: (a: OperatorAnnotation[]) => void;
  judgements:          Record<string, JudgementEntry>;
  annotations:         OperatorAnnotation[];
}

// Panel wrapper — the translucent card used across the analysis column.
function Card({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 14, padding: 13 }}>
      {children}
    </div>
  );
}

const META_LABEL: React.CSSProperties = { fontSize: 10, textTransform: "uppercase", letterSpacing: "0.1em", color: "#6b7a93" };
const META_VALUE: React.CSSProperties = { fontSize: 13, color: "#cbd5e1", fontWeight: 500 };

export function VerdictPanel({
  scan, onJudgementsChange, onAnnotationsChange, judgements, annotations,
}: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [frameIdx, setFrameIdx]     = useState(0);

  const det    = scan.detection;
  const verd   = scan.verdict;
  const frames = det?.frames ?? [];
  const frame  = frames[frameIdx] ?? null;

  // Lifecycle flags
  const isAnalyzing = scan.state === "analyzing" || scan.state === "pending";
  const isFailed    = det?.status === "failed" || scan.state === "error";
  const isNoFind    = det?.status === "completed_no_findings";

  const detections: Detection[] = [...(det?.detections ?? [])].sort((a, b) => {
    const sevDiff = CATEGORY_SEVERITY[b.category] - CATEGORY_SEVERITY[a.category];
    return sevDiff !== 0 ? sevDiff : b.score - a.score;
  });
  const isPresent = detections.length > 0 && !isFailed && !isAnalyzing;

  const verdictMap = Object.fromEntries((verd?.per_detection ?? []).map((dv) => [dv.detection_id, dv]));

  const handleJudge = (detId: string, j: DetectionJudgement, corrected?: ThreatCategory) => {
    onJudgementsChange({ ...judgements, [detId]: { judgement: j, corrected: corrected ?? null } });
  };
  const handleAddAnnotation = (a: Omit<OperatorAnnotation, "note_uz"> & { note_uz: string | null }) => {
    onAnnotationsChange([...annotations, a as OperatorAnnotation]);
  };
  const removeAnnotation = (idx: number) => onAnnotationsChange(annotations.filter((_, i) => i !== idx));

  const risk = scan.overall_risk ?? "clear";
  const count = detections.length;
  const bannerSub =
    isAnalyzing ? "Detektor ishlamoqda — natija kutilmoqda"
    : isFailed ? "Model tasdiqlangan javob qaytarmadi"
    : isNoFind ? "Shubhali buyum topilmadi — bu ozod etish emas"
    : `${count} ta topilma aniqlandi${risk === "high" ? " · jismoniy ko'rik tavsiya etiladi" : ""}`;

  return (
    <div className="flex flex-col" style={{ gap: 14 }}>

      {/* Risk banner */}
      <AlertBanner risk={risk} sub={bannerSub} />

      {/* Metadata strip */}
      <div className="flex flex-wrap" style={{ gap: 26, padding: "0 4px" }}>
        <div><div style={META_LABEL}>Subyekt</div><div style={META_VALUE}>{SCAN_SUBJECT[scan.subject]}</div></div>
        <div><div style={META_LABEL}>Modallik</div><div style={META_VALUE}>{IMAGE_MODALITY[scan.modality]}</div></div>
        {scan.lane_id && <div><div style={META_LABEL}>Yo'lak</div><div style={META_VALUE}>{scan.lane_id}</div></div>}
        <div><div style={META_LABEL}>Skan ID</div><div className="font-mono" style={{ fontSize: 13, color: "#94a3b8" }} title={scan.scan_id}>{scan.scan_id.slice(0, 12)}…</div></div>
      </div>

      {/* Two sub-columns: viewer + analysis */}
      <div className="flex items-start" style={{ gap: 14 }}>

        {/* Viewer */}
        <div className="flex-1 min-w-0 flex flex-col" style={{ gap: 10 }}>
          {frames.length > 0 && (
            <div className="flex" style={{ gap: 6 }}>
              {frames.map((f, i) => {
                const active = i === frameIdx;
                return (
                  <div key={f.frame_id} onClick={() => setFrameIdx(i)}
                    className="flex flex-col cursor-pointer"
                    style={{
                      gap: 1, padding: "6px 12px", borderRadius: 8, fontSize: 12,
                      border: `1px solid ${active ? "rgba(255,255,255,0.2)" : "rgba(255,255,255,0.07)"}`,
                      background: active ? "rgba(255,255,255,0.09)" : "rgba(255,255,255,0.02)",
                      color: active ? "#e2e8f0" : "#8595ad",
                    }}>
                    <span style={{ fontWeight: 600 }}>Kadr {i + 1}</span>
                    <span className="font-mono" style={{ fontSize: 10, opacity: 0.7 }}>{f.view_label ?? "high_energy"}</span>
                  </div>
                );
              })}
            </div>
          )}

          <XRayViewer
            scanId={scan.scan_id}
            frame={frame}
            detections={detections.filter((d) => d.frame_id === frame?.frame_id)}
            selectedId={selectedId}
            onSelect={setSelectedId}
            analyzing={isAnalyzing}
            onAddAnnotation={handleAddAnnotation}
          />
        </div>

        {/* Analysis column */}
        <div className="shrink-0 flex flex-col" style={{ width: 300, gap: 12 }}>

          {/* Detected items */}
          <Card>
            <div className="flex items-center justify-between" style={{ marginBottom: 11 }}>
              <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: "-0.01em" }}>{DETECTIONS_TITLE}</span>
              {isPresent && (
                <span style={{ fontSize: 11, fontWeight: 600, padding: "1px 9px", borderRadius: 999, background: "rgba(255,255,255,0.08)", color: "#cbd5e1" }}>{count}</span>
              )}
            </div>

            {isPresent && (
              <div className="flex flex-col" style={{ gap: 10 }}>
                {detections.map((d) => (
                  <DetectionCard
                    key={d.detection_id}
                    detection={d}
                    verdict={verdictMap[d.detection_id]}
                    judgement={judgements[d.detection_id]?.judgement ?? "unreviewed"}
                    corrected={judgements[d.detection_id]?.corrected}
                    selected={selectedId === d.detection_id}
                    onSelect={() => setSelectedId((s) => s === d.detection_id ? null : d.detection_id)}
                    onJudge={(j, c) => handleJudge(d.detection_id, j, c)}
                  />
                ))}
              </div>
            )}

            {isNoFind && (
              <div style={{ padding: 14, borderRadius: 11, background: "rgba(148,163,184,0.07)", border: "1px solid rgba(148,163,184,0.22)" }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "#cbd5e1", marginBottom: 5 }}>Shubhali buyum aniqlanmadi</div>
                <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.5 }}>Bu ko'rikdan ozod etishni anglatmaydi. Yakuniy qaror operatorga tegishli.</div>
              </div>
            )}

            {isAnalyzing && (
              <div className="flex flex-col animate-pulse" style={{ gap: 8 }}>
                <div style={{ height: 58, borderRadius: 11, background: "rgba(255,255,255,0.04)" }} />
                <div style={{ height: 58, borderRadius: 11, background: "rgba(255,255,255,0.03)" }} />
              </div>
            )}

            {isFailed && (
              <div style={{ padding: 14, borderRadius: 11, background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.3)" }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "#fbbf24", marginBottom: 5 }}>Tahlil bajarilmadi</div>
                <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.5 }}>Natija ishonchli emas. Tasvirni qo'lda baholang.</div>
              </div>
            )}
          </Card>

          {/* System conclusion */}
          <Card>
            <div className="flex items-center" style={{ gap: 7, marginBottom: 10 }}>
              <Info color="#818cf8" />
              <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: "-0.01em" }}>{VERDICT_TITLE}</span>
            </div>

            {verd && (
              <>
                <div style={{ fontSize: 13, color: "#cbd5e1", lineHeight: 1.6, marginBottom: 11 }}>{verd.summary_uz}</div>
                {verd.model && (
                  <div className="font-mono" style={{ fontSize: 11, color: "#7c8aa3", marginBottom: 11 }}>
                    model: {verd.model.name} · v{verd.model.version}
                  </div>
                )}
                <div className="flex" style={{ gap: 8, padding: 10, borderRadius: 9, background: "rgba(99,102,241,0.08)", border: "1px solid rgba(99,102,241,0.22)" }}>
                  <Info color="#a5b4fc" small />
                  <span style={{ fontSize: 11.5, color: "#a5b4fc", lineHeight: 1.5 }}>{VERDICT_ADVISORY_NOTE}</span>
                </div>
              </>
            )}

            {!verd && isAnalyzing && (
              <div className="flex items-center animate-pulse" style={{ gap: 9, color: "#8595ad", fontSize: 13 }}>
                <span className="animate-spin" style={{ width: 16, height: 16, border: "2px solid rgba(148,163,184,0.3)", borderTopColor: "#94a3b8", borderRadius: 999 }} />
                {/* VERDICT_PENDING */}Xulosa tayyorlanmoqda…
              </div>
            )}

            {!verd && !isAnalyzing && (
              <div style={{ fontSize: 13, fontWeight: 600, color: "#fbbf24" }}>{VERDICT_UNAVAILABLE}</div>
            )}
          </Card>

          {/* Missed items */}
          <Card>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: "-0.01em", marginBottom: 10 }}>{MISSED_TITLE}</div>
            {annotations.length > 0 ? (
              <div className="flex flex-col" style={{ gap: 8 }}>
                {annotations.map((a, i) => {
                  const color = catColor(a.category);
                  return (
                    <div key={i} style={{ background: "rgba(245,158,11,0.07)", border: "1px solid rgba(245,158,11,0.3)", borderRadius: 10, padding: 10 }}>
                      <div className="flex items-center justify-between" style={{ marginBottom: 6 }}>
                        <div className="flex items-center" style={{ gap: 7 }}>
                          <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} aria-hidden="true" />
                          <span style={{ fontSize: 12.5, fontWeight: 600, color }}>{THREAT_CATEGORY[a.category]}</span>
                        </div>
                        <span onClick={() => removeAnnotation(i)} role="button" aria-label={MISSED_DELETE}
                          style={{ cursor: "pointer", color: "#7c8aa3", fontSize: 15, lineHeight: 1, width: 18, textAlign: "center" }}>×</span>
                      </div>
                      <div className="font-mono" style={{ fontSize: 10.5, color: "#7c8aa3" }}>
                        x:{a.box.x} y:{a.box.y} w:{a.box.width} h:{a.box.height}
                      </div>
                      {a.note_uz && <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>{a.note_uz}</div>}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "#5b6679", lineHeight: 1.5 }}>
                Detektor o'tkazib yuborgan buyum bo'lsa, viewer ustida «Chizish» orqali belgilang.
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

// Small info glyph used in the conclusion card.
function Info({ color, small }: { color: string; small?: boolean }) {
  const s = small ? 15 : 15;
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
      width={s} height={s} style={{ flex: "none", marginTop: small ? 1 : 0 }} aria-hidden="true">
      <circle cx="12" cy="12" r="9" /><path d="M12 16v-4" /><path d="M12 8h.01" />
    </svg>
  );
}
