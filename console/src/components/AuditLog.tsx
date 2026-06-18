import { useState } from "react";
import { ShieldCheck, ShieldAlert, ChevronDown, ChevronUp } from "lucide-react";
import type { AuditEntry } from "../lib/api";
import {
  AUDIT_TITLE, AUDIT_EMPTY, AUDIT_TIME, AUDIT_OPERATOR,
  AUDIT_EVENT, AUDIT_SCAN_ID, AUDIT_VALID, AUDIT_TAMPERED,
} from "../lib/uz";

const EVENT_TYPE_UZ: Record<string, string> = {
  acquisition_recorded: "Tasvirni qabul qilish",
  detection_recorded:   "Tahlil natijasi",
  verdict_recorded:     "Xulosa",
  feedback_recorded:    "Operator qarori",
  scan_reviewing:       "Ko'rib chiqish boshlandi",
};

interface Props {
  entries:        AuditEntry[];
  chainValid:     boolean | null;
}

export function AuditLog({ entries, chainValid }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <section className="flex flex-col gap-2" aria-labelledby="audit-heading">
      <div className="flex items-center justify-between">
        <h2 id="audit-heading" className="text-sm font-semibold text-content-primary">
          {AUDIT_TITLE}
        </h2>
        {chainValid !== null && (
          <span
            className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded ${
              chainValid
                ? "text-green-400 bg-green-900/30"
                : "text-red-400 bg-red-900/30"
            }`}
            role="status"
          >
            {chainValid
              ? <><ShieldCheck size={12} aria-hidden="true" /> {AUDIT_VALID}</>
              : <><ShieldAlert size={12} aria-hidden="true" /> {AUDIT_TAMPERED}</>
            }
          </span>
        )}
      </div>

      {entries.length === 0 && (
        <p className="text-xs text-content-muted">{AUDIT_EMPTY}</p>
      )}

      <div className="space-y-1">
        {entries.map((e) => {
          const isOpen = expanded.has(e.event_id);
          const tzLabel = new Date(e.created_at).toLocaleString("uz-Latn-UZ", {
            dateStyle: "short", timeStyle: "medium",
          });
          return (
            <div
              key={e.event_id}
              className="rounded border border-surface-border bg-surface-card overflow-hidden"
            >
              <button
                className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-surface-hover transition-colors"
                onClick={() => toggle(e.event_id)}
                aria-expanded={isOpen}
              >
                <span className="text-xs font-mono text-content-muted w-5 shrink-0 tabular-nums">
                  {e.seq}
                </span>
                <div className="flex-1 min-w-0 flex items-center gap-3 flex-wrap">
                  <span className="text-xs font-medium text-content-primary">
                    {EVENT_TYPE_UZ[e.event_type] ?? e.event_type}
                  </span>
                  {e.operator_id && (
                    <span className="text-xs text-content-muted font-mono truncate max-w-[80px]">
                      {e.operator_id.slice(0, 8)}
                    </span>
                  )}
                  <span className="text-xs text-content-muted ml-auto shrink-0">{tzLabel}</span>
                </div>
                <span className="text-content-muted shrink-0">
                  {isOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                </span>
              </button>

              {isOpen && (
                <div className="px-3 pb-2 border-t border-surface-border space-y-1.5 pt-2">
                  <Row label={AUDIT_SCAN_ID} value={e.event_id} mono />
                  {e.operator_id && <Row label={AUDIT_OPERATOR} value={e.operator_id} mono />}
                  <Row label={AUDIT_TIME}    value={tzLabel} />
                  <Row label={AUDIT_EVENT}   value={e.event_type} mono />
                  {/* HMAC fingerprint */}
                  <Row
                    label="HMAC"
                    value={`${e.event_hmac.slice(0, 16)}…`}
                    mono
                  />
                  {/* Selected payload keys */}
                  {Object.entries(e.payload).slice(0, 4).map(([k, v]) => (
                    <Row
                      key={k}
                      label={k}
                      value={typeof v === "string" ? v : JSON.stringify(v)}
                      mono={typeof v === "string" && v.length > 12}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-2 text-xs">
      <span className="w-24 shrink-0 text-content-muted">{label}</span>
      <span className={`text-content-secondary truncate ${mono ? "font-mono" : ""}`}>{value}</span>
    </div>
  );
}
