import { useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import type {
  ScanRecord, OperatorOutcome, DetectionJudgement, ThreatCategory,
  OperatorAnnotation, OperatorFeedback, DetectionReview,
} from "../lib/types";
import { submitFeedback, markReviewing, ApiError } from "../lib/api";
import { IS_MOCK } from "../lib/mock";
import { OUTCOME_COLOR, hexA } from "../lib/theme";
import {
  DECISION_NOTE_HINT,
  DECISION_SUBMIT, DECISION_SUBMITTING, DECISION_ALREADY_MADE,
  OUTCOME_LABEL,
  CONFIRM_CLEARED, CONFIRM_SEIZED, CONFIRM_NO,
  CONFIRM_CLEARED_HIGH, SEIZED_NOTE_REQUIRED,
  SR_DECISION_LOGGED,
} from "../lib/uz";

interface JudgementEntry {
  judgement: DetectionJudgement;
  corrected: ThreatCategory | null;
}

interface Props {
  scan:        ScanRecord;
  operatorId:  string;
  judgements:  Record<string, JudgementEntry>;
  annotations: OperatorAnnotation[];
  onDecided:   (updated: ScanRecord) => void;
}

const OUTCOMES: OperatorOutcome[] = ["inspected", "cleared", "seized", "escalated"];

export function DecisionPanel({ scan, operatorId, judgements, annotations, onDecided }: Props) {
  const [outcome, setOutcome]       = useState<OperatorOutcome | null>(null);
  const [notes, setNotes]           = useState("");
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError]           = useState<string | null>(null);
  const [done, setDone]             = useState(false);
  const [liveRegion, setLiveRegion] = useState("");

  const alreadyDecided = scan.state === "decided";
  const isHighRisk     = scan.overall_risk === "high";

  // Clearing a HIGH-risk scan is a conflicting decision → stronger prompt.
  const confirmMessage = (o: OperatorOutcome): string | undefined => {
    if (o === "cleared") return isHighRisk ? CONFIRM_CLEARED_HIGH : CONFIRM_CLEARED;
    if (o === "seized")  return CONFIRM_SEIZED;
    return undefined;
  };
  const confirmTitle = (o: OperatorOutcome): string =>
    o === "cleared" && isHighRisk
      ? "Diqqat — yuqori xavfli skanni o'tkazyapsiz"
      : "Musodarani tasdiqlang";

  const noteRequired = (o: OperatorOutcome | null): boolean => o === "seized";
  const noteMissing  = noteRequired(outcome) && notes.trim().length === 0;

  const handleOutcomeClick = (o: OperatorOutcome) => { setOutcome(o); setConfirming(false); setError(null); };

  const handleSubmit = () => {
    if (!outcome) return;
    if (noteRequired(outcome) && notes.trim().length === 0) { setError(SEIZED_NOTE_REQUIRED); return; }
    if (confirmMessage(outcome) && !confirming) { setConfirming(true); return; }
    void _submit(outcome);
  };

  const _submit = async (selectedOutcome: OperatorOutcome) => {
    if (!scan.detection) { setError("Tahlil natijasi mavjud emas. Qaror qilib bo'lmaydi."); return; }
    setSubmitting(true);
    setError(null);
    setConfirming(false);
    const now = new Date().toISOString();

    const reviews: DetectionReview[] = scan.detection.detections.map((d) => {
      const entry = judgements[d.detection_id];
      return {
        detection_id:       d.detection_id,
        judgement:          entry?.judgement ?? "unreviewed",
        corrected_category: entry?.corrected ?? null,
        note_uz:            null,
      };
    });

    const feedback: OperatorFeedback = {
      schema_version: "1.0",
      feedback_id:    crypto.randomUUID(),
      scan_id:        scan.scan_id,
      verdict_id:     scan.verdict?.verdict_id ?? null,
      operator_id:    operatorId,
      detection:      scan.detection,
      outcome:        selectedOutcome,
      reviews,
      missed:         annotations,
      decided_at:     now,
      emitted_at:     now,
      notes_uz:       notes.trim() || null,
    };

    try {
      if (!IS_MOCK) {
        // The state machine requires verdicted/analyzed → reviewing (scan.opened)
        // before feedback can be banked (feedback.banked → decided). The redesign
        // has no separate "mark reviewed" button, so we transition here. Ignore
        // the error if the scan is already in `reviewing`.
        if (scan.state === "verdicted" || scan.state === "analyzed") {
          try { await markReviewing(scan.scan_id); } catch { /* already reviewing */ }
        }
        await submitFeedback(feedback);
      }
      onDecided({ ...scan, state: "decided", decided_at: now });
      setDone(true);
      setLiveRegion(SR_DECISION_LOGGED);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Qarorni saqlashda xato yuz berdi.");
    } finally {
      setSubmitting(false);
    }
  };

  // ── Header (eyebrow + indigo diamond + title) ──
  const header = (
    <>
      <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.16em", color: "#7c8aa3", fontWeight: 600, marginBottom: 3 }}>Operator</div>
      <div className="flex items-center" style={{ gap: 8, marginBottom: 16 }}>
        <span style={{ width: 14, height: 14, borderRadius: 4, background: "linear-gradient(135deg,#818cf8,#6366f1)", transform: "rotate(45deg)" }} aria-hidden="true" />
        <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: "-0.02em" }}>Operator qarori</span>
      </div>
    </>
  );

  // ── Done / already-decided ──
  if (alreadyDecided || done) {
    const col = done && outcome ? OUTCOME_COLOR[outcome] : null;
    return (
      <div style={{ padding: "16px 15px" }}>
        {header}
        <div style={{ padding: 18, borderRadius: 14, background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.1)", textAlign: "center" }}>
          <div className="flex justify-center" style={{ marginBottom: 12, color: "#22c55e" }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" width={40} height={40} aria-hidden="true">
              <circle cx="12" cy="12" r="9" /><path d="m8.5 12.5 2.5 2.5 4.5-5" />
            </svg>
          </div>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 9 }}>{DECISION_ALREADY_MADE}</div>
          {col && outcome && (
            <div className="inline-flex items-center" style={{
              gap: 7, padding: "5px 12px", borderRadius: 999, fontSize: 12.5, fontWeight: 600,
              color: col, background: hexA(col, 0.14), border: `1px solid ${hexA(col, 0.4)}`,
            }}>{OUTCOME_LABEL[outcome]}</div>
          )}
          {scan.decided_at && (
            <div className="font-mono" style={{ fontSize: 12, color: "#7c8aa3", marginTop: 11 }}>
              {new Date(scan.decided_at).toLocaleString("uz-Latn-UZ")}
            </div>
          )}
          {done && notes.trim() && (
            <div style={{ marginTop: 12, padding: 10, borderRadius: 9, background: "rgba(0,0,0,0.2)", fontSize: 12.5, color: "#94a3b8", textAlign: "left", lineHeight: 1.5 }}>{notes.trim()}</div>
          )}
          <div style={{ marginTop: 14, fontSize: 11, color: "#5b6679" }}>Yozuv qulflangan · faqat o'qish</div>
        </div>
        <span className="sr-only" aria-live="polite">{liveRegion}</span>
      </div>
    );
  }

  const seize = outcome === "seized";
  const submitEnabled = !!outcome && !submitting && !noteMissing;
  const confColor = outcome === "cleared" && isHighRisk ? "#ef4444" : "#f59e0b";

  return (
    <div style={{ padding: "16px 15px" }}>
      {header}

      {/* Outcome selector */}
      <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.1em", color: "#6b7a93", fontWeight: 600, marginBottom: 8 }}>Yakuniy harakat</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 9, marginBottom: 15 }}>
        {OUTCOMES.map((o) => {
          const c = OUTCOME_COLOR[o];
          const active = outcome === o;
          return (
            <button key={o} onClick={() => handleOutcomeClick(o)} aria-pressed={active}
              className="flex flex-col items-start text-left"
              style={{
                gap: 9, padding: 13, borderRadius: 12, cursor: "pointer", minHeight: 84,
                border: `1px solid ${active ? c : "rgba(255,255,255,0.10)"}`,
                background: active ? hexA(c, 0.13) : "rgba(255,255,255,0.03)",
                boxShadow: active ? `0 0 0 1px ${c},0 10px 28px ${hexA(c, 0.22)}` : undefined,
                color: "#e2e8f0", transition: "all .15s",
              }}>
              <span style={{ width: 10, height: 10, borderRadius: 3, background: c, boxShadow: `0 0 8px ${hexA(c, active ? 0.8 : 0.3)}` }} aria-hidden="true" />
              <span style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.3 }}>{OUTCOME_LABEL[o]}</span>
            </button>
          );
        })}
      </div>

      {/* Notes */}
      <div style={{ marginBottom: 14 }}>
        <div className="flex items-center" style={{ gap: 5, marginBottom: 6 }}>
          <span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "#8595ad", fontWeight: 600 }}>Izoh</span>
          {seize
            ? <span style={{ color: "#f87171", fontWeight: 700 }}>*</span>
            : <span style={{ fontSize: 11, color: "#5b6679" }}>(ixtiyoriy)</span>}
        </div>
        <textarea
          id="decision-notes"
          rows={3}
          value={notes}
          onChange={(e) => { setNotes(e.target.value); if (error) setError(null); }}
          placeholder={DECISION_NOTE_HINT}
          maxLength={2000}
          aria-required={seize}
          aria-invalid={noteMissing}
          style={{
            width: "100%", padding: "10px 11px", borderRadius: 10, background: "rgba(0,0,0,0.25)",
            border: `1px solid ${noteMissing ? "rgba(248,113,113,0.6)" : "rgba(255,255,255,0.12)"}`,
            color: "#e2e8f0", fontSize: 13, lineHeight: 1.5, resize: "vertical",
          }}
        />
      </div>

      {/* Confirmation prompt */}
      {confirming && outcome && confirmMessage(outcome) && (
        <div className={outcome === "cleared" && isHighRisk ? "halo-high" : ""}
          style={{ padding: 13, borderRadius: 12, background: hexA(confColor, 0.12), border: `1px solid ${hexA(confColor, 0.45)}`, marginBottom: 14 }}>
          <div className="flex items-start" style={{ gap: 8, marginBottom: 8 }}>
            <span style={{ color: confColor, flex: "none", marginTop: 1 }}><AlertTriangle size={17} aria-hidden="true" /></span>
            <div style={{ fontSize: 13.5, fontWeight: 700, color: confColor, lineHeight: 1.35 }}>{confirmTitle(outcome)}</div>
          </div>
          <div style={{ fontSize: 12.5, color: "#cbd5e1", lineHeight: 1.55, marginBottom: 12 }}>{confirmMessage(outcome)}</div>
          <div className="flex" style={{ gap: 8 }}>
            <button onClick={() => void _submit(outcome)}
              style={{ flex: 1, padding: 9, fontSize: 13, fontWeight: 600, borderRadius: 9, cursor: "pointer", border: `1px solid ${confColor}`, background: hexA(confColor, 0.2), color: confColor }}>
              {outcome === "cleared" && isHighRisk ? "Ha, o'tkazaman" : "Ha, tasdiqlayman"}
            </button>
            <button onClick={() => setConfirming(false)}
              style={{ flex: 1, padding: 9, fontSize: 13, fontWeight: 600, borderRadius: 9, cursor: "pointer", border: "1px solid rgba(255,255,255,0.14)", background: "rgba(255,255,255,0.04)", color: "#aebbcf" }}>
              {CONFIRM_NO}
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {error && <p className="animate-fade-in" role="alert" style={{ fontSize: 13, color: "#fca5a5", marginBottom: 12 }}>{error}</p>}

      {/* Submit */}
      {!confirming && (
        <button
          onClick={handleSubmit}
          disabled={!submitEnabled}
          aria-busy={submitting}
          className="flex items-center justify-center"
          style={{
            width: "100%", padding: 13, fontSize: 14, fontWeight: 600, borderRadius: 11, border: "none",
            gap: 8, cursor: submitEnabled ? "pointer" : "not-allowed", color: "#fff",
            background: submitEnabled ? "linear-gradient(135deg,#6366f1,#4f46e5)" : "rgba(255,255,255,0.06)",
            opacity: submitEnabled ? 1 : 0.5,
            boxShadow: submitEnabled ? "0 10px 28px rgba(99,102,241,0.35)" : "none",
            transition: "all .15s",
          }}
        >
          {submitting && <Loader2 size={15} className="animate-spin" aria-hidden="true" />}
          {submitting ? DECISION_SUBMITTING : DECISION_SUBMIT}
        </button>
      )}

      <div style={{ marginTop: 11, fontSize: 11, color: "#5b6679", lineHeight: 1.5, textAlign: "center" }}>
        Bu yagona qaror nuqtasi. Model faqat maslahat beradi — yakuniy qaror operatorniki.
      </div>
      <span className="sr-only" aria-live="polite">{liveRegion}</span>
    </div>
  );
}
