import { useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Gavel } from "lucide-react";
import type {
  ScanRecord, OperatorOutcome, DetectionJudgement, ThreatCategory,
  OperatorAnnotation, OperatorFeedback, DetectionReview,
} from "../lib/types";
import { submitFeedback, ApiError } from "../lib/api";
import { IS_MOCK } from "../lib/mock";
import {
  DECISION_TITLE, DECISION_SUBTITLE,
  DECISION_NOTE_LABEL, DECISION_NOTE_HINT,
  DECISION_SUBMIT, DECISION_SUBMITTING, DECISION_ALREADY_MADE,
  OUTCOME_LABEL, OUTCOME_DESC,
  CONFIRM_CLEARED, CONFIRM_SEIZED, CONFIRM_YES, CONFIRM_NO,
  CONFIRM_CLEARED_HIGH, SEIZED_NOTE_REQUIRED,
  SR_DECISION_LOGGED,
} from "../lib/uz";

// ------------------------------------------------------------------
interface JudgementEntry {
  judgement:  DetectionJudgement;
  corrected:  ThreatCategory | null;
}

interface Props {
  scan:          ScanRecord;
  operatorId:    string;
  judgements:    Record<string, JudgementEntry>;
  annotations:   OperatorAnnotation[];
  onDecided:     (updated: ScanRecord) => void;
}

const OUTCOMES: OperatorOutcome[] = ["inspected", "cleared", "seized", "escalated"];

const OUTCOME_STYLE: Record<OperatorOutcome, string> = {
  inspected:  "border-blue-700 bg-blue-900/30 hover:bg-blue-800/40 text-blue-300",
  cleared:    "border-green-800 bg-green-900/30 hover:bg-green-800/40 text-green-300",
  seized:     "border-red-800 bg-red-900/30 hover:bg-red-800/40 text-red-300",
  escalated:  "border-amber-800 bg-amber-900/30 hover:bg-amber-800/40 text-amber-300",
};

const OUTCOME_ACTIVE: Record<OperatorOutcome, string> = {
  inspected:  "border-blue-500 bg-blue-800/50 ring-1 ring-blue-600",
  cleared:    "border-green-600 bg-green-800/50 ring-1 ring-green-600",
  seized:     "border-red-600 bg-red-800/50 ring-1 ring-red-600",
  escalated:  "border-amber-600 bg-amber-800/50 ring-1 ring-amber-600",
};

const NEEDS_CONFIRM: Partial<Record<OperatorOutcome, string>> = {
  cleared: CONFIRM_CLEARED,
  seized:  CONFIRM_SEIZED,
};

// ------------------------------------------------------------------
export function DecisionPanel({ scan, operatorId, judgements, annotations, onDecided }: Props) {
  const [outcome,     setOutcome]     = useState<OperatorOutcome | null>(null);
  const [notes,       setNotes]       = useState("");
  const [confirming,  setConfirming]  = useState(false);
  const [submitting,  setSubmitting]  = useState(false);
  const [error,       setError]       = useState<string | null>(null);
  const [done,        setDone]        = useState(false);
  const [liveRegion,  setLiveRegion]  = useState("");

  const alreadyDecided = scan.state === "decided";
  const isHighRisk     = scan.overall_risk === "high";

  // The confirmation copy depends on the outcome AND the system's risk band:
  // clearing a HIGH-risk scan is a conflicting decision and gets a stronger,
  // explicitly conflict-flagging prompt.
  const confirmMessage = (o: OperatorOutcome): string | undefined => {
    if (o === "cleared") return isHighRisk ? CONFIRM_CLEARED_HIGH : CONFIRM_CLEARED;
    if (o === "seized")  return CONFIRM_SEIZED;
    return NEEDS_CONFIRM[o];
  };

  // A note (justification) is mandatory for seizure.
  const noteRequired = (o: OperatorOutcome | null): boolean => o === "seized";
  const noteMissing  = noteRequired(outcome) && notes.trim().length === 0;

  const handleOutcomeClick = (o: OperatorOutcome) => {
    setOutcome(o);
    setConfirming(false);   // reset confirm if switching
    setError(null);
  };

  const handleSubmit = () => {
    if (!outcome) return;
    if (noteRequired(outcome) && notes.trim().length === 0) {
      setError(SEIZED_NOTE_REQUIRED);
      return;
    }
    if (confirmMessage(outcome) && !confirming) {
      setConfirming(true);
      return;
    }
    void _submit(outcome);
  };

  const _submit = async (selectedOutcome: OperatorOutcome) => {
    if (!scan.detection) {
      setError("Tahlil natijasi mavjud emas. Qaror qilib bo'lmaydi.");
      return;
    }

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
        await submitFeedback(feedback);
      }
      // Optimistically update the scan record
      const updatedScan: ScanRecord = {
        ...scan,
        state:      "decided",
        decided_at: now,
      };
      onDecided(updatedScan);
      setDone(true);
      setLiveRegion(SR_DECISION_LOGGED);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Qarorni saqlashda xato yuz berdi.");
    } finally {
      setSubmitting(false);
    }
  };

  // ------------------------------------------------------------------
  if (alreadyDecided || done) {
    return (
      <div className="section-decide rounded-xl border border-white/10 glass section-tint p-4 flex items-center gap-3 animate-rise-in">
        <CheckCircle2 size={20} className="text-green-400 shrink-0" aria-hidden="true" />
        <div>
          <p className="text-sm font-semibold text-content-primary">{DECISION_ALREADY_MADE}</p>
          {scan.decided_at && (
            <p className="text-xs text-content-muted mt-0.5">
              {new Date(scan.decided_at).toLocaleString("uz-Latn-UZ")}
            </p>
          )}
        </div>
        <span className="sr-only" aria-live="polite">{liveRegion}</span>
      </div>
    );
  }

  return (
    <div className="section-decide rounded-xl border border-white/10 glass section-tint p-4 space-y-4 shadow-elev-3">
      {/* Header — indigo "operator command" identity */}
      <div className="flex items-center gap-2.5">
        <span className="grid place-items-center w-8 h-8 rounded-lg section-tile shrink-0" aria-hidden="true">
          <Gavel size={16} />
        </span>
        <div className="section-bar pl-3">
          <p className="text-[10px] font-semibold uppercase section-eyebrow leading-none">Operator</p>
          <h2 className="text-sm font-bold text-content-primary leading-tight mt-0.5">{DECISION_TITLE}</h2>
          <p className="text-xs text-content-muted mt-0.5">{DECISION_SUBTITLE}</p>
        </div>
      </div>
      <div className="divider-soft" aria-hidden="true" />

      {/* Outcome selector */}
      <div className="grid grid-cols-2 gap-2 scene">
        {OUTCOMES.map((o) => (
          <button
            key={o}
            onClick={() => handleOutcomeClick(o)}
            aria-pressed={outcome === o}
            className={`card-3d press flex flex-col gap-0.5 p-3 rounded-xl border text-left ${
              outcome === o ? `${OUTCOME_ACTIVE[o]} shadow-elev-3` : `${OUTCOME_STYLE[o]} shadow-elev-1`
            }`}
          >
            <span className="text-sm font-semibold">{OUTCOME_LABEL[o]}</span>
            <span className="text-xs opacity-70 leading-snug">{OUTCOME_DESC[o]}</span>
          </button>
        ))}
      </div>

      {/* Notes */}
      <div>
        <label className="block text-xs text-content-secondary mb-1" htmlFor="decision-notes">
          {DECISION_NOTE_LABEL}
          {noteRequired(outcome) && <span className="text-red-400"> *</span>}
        </label>
        <textarea
          id="decision-notes"
          rows={2}
          value={notes}
          onChange={(e) => { setNotes(e.target.value); if (error) setError(null); }}
          placeholder={DECISION_NOTE_HINT}
          maxLength={2000}
          aria-required={noteRequired(outcome)}
          aria-invalid={noteMissing}
          className={`w-full bg-surface-border/50 border rounded-lg px-3 py-2 text-sm text-content-primary placeholder-content-muted resize-none focus:outline-none focus:ring-1 ${
            noteMissing ? "border-red-700 focus:ring-red-600" : "border-surface-border focus:ring-indigo-500"
          }`}
        />
      </div>

      {/* Confirmation prompt */}
      {confirming && outcome && confirmMessage(outcome) && (
        <div className={`flex items-start gap-3 p-3 rounded-xl border animate-rise-in ${
          outcome === "cleared" && isHighRisk
            ? "border-red-600 bg-red-900/30 halo-high"
            : "border-amber-700 bg-amber-900/20 shadow-glow-medium"
        }`}>
          <AlertTriangle
            size={16}
            className={`shrink-0 mt-0.5 ${outcome === "cleared" && isHighRisk ? "text-red-400" : "text-amber-400"}`}
            aria-hidden="true"
          />
          <div className="flex-1">
            <p className={`text-sm ${outcome === "cleared" && isHighRisk ? "text-red-200 font-semibold" : "text-amber-200"}`}>
              {confirmMessage(outcome)}
            </p>
            <div className="flex gap-2 mt-2">
              <button
                onClick={() => void _submit(outcome)}
                className={`press px-3 py-1.5 rounded-lg text-sm font-semibold text-white shadow-elev-2 transition-all ${
                  outcome === "cleared" && isHighRisk
                    ? "bg-gradient-to-b from-red-500 to-red-600 hover:from-red-400 hover:to-red-500"
                    : "bg-gradient-to-b from-amber-500 to-amber-600 hover:from-amber-400 hover:to-amber-500"
                }`}
              >
                {CONFIRM_YES}
              </button>
              <button
                onClick={() => setConfirming(false)}
                className="px-3 py-1.5 rounded text-sm text-content-secondary hover:text-content-primary transition-colors"
              >
                {CONFIRM_NO}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <p className="text-sm text-red-400 animate-fade-in" role="alert">{error}</p>
      )}

      {/* Submit */}
      {!confirming && (
        <button
          onClick={handleSubmit}
          disabled={!outcome || submitting || noteMissing}
          className={`press w-full py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center justify-center gap-2 ${
            outcome && !submitting && !noteMissing
              ? "bg-gradient-to-b from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white shadow-elev-2 hover:shadow-glow-indigo"
              : "bg-surface-border text-content-muted cursor-not-allowed"
          }`}
          aria-busy={submitting}
        >
          {submitting && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
          {submitting ? DECISION_SUBMITTING : DECISION_SUBMIT}
        </button>
      )}

      <span className="sr-only" aria-live="polite">{liveRegion}</span>
    </div>
  );
}
