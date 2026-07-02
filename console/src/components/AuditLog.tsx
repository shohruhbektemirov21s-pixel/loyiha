import type { AuditEntry } from "../lib/api";
import { AUDIT_EMPTY, AUDIT_TAMPERED } from "../lib/uz";
import { hexA, OUTCOME_COLOR } from "../lib/theme";

const EVENT_TYPE_UZ: Record<string, string> = {
  acquisition_recorded: "Tasvirni qabul qilish",
  detection_recorded:   "Tahlil natijasi",
  detection_failed:     "Tahlil xatosi",
  verdict_recorded:     "Xulosa",
  feedback_recorded:    "Operator qarori",
  scan_reviewing:       "Ko'rib chiqish boshlandi",
};

interface Props {
  entries:        AuditEntry[];
  chainValid:     boolean | null;
}

// ── derive a stable accent colour from the event type / payload ──
function entryColor(e: AuditEntry): string {
  const t = e.event_type;
  if (t.startsWith("acquisition")) return "#38bdf8";
  if (t === "detection_recorded") return "#c084fc";
  if (t === "detection_failed" || t.endsWith("failed")) return "#f59e0b";
  if (t.startsWith("verdict")) {
    const outcome = e.payload?.outcome;
    if (typeof outcome === "string" && outcome in OUTCOME_COLOR) {
      return OUTCOME_COLOR[outcome as keyof typeof OUTCOME_COLOR];
    }
    return "#818cf8";
  }
  if (t.startsWith("feedback")) return "#64748b";
  return "#94a3b8";
}

// ── short human summary from the entry payload ──
function entrySummary(e: AuditEntry): string {
  const p = e.payload ?? {};
  const parts: string[] = [];

  const detCount =
    typeof p.detection_count === "number"
      ? p.detection_count
      : Array.isArray(p.detections)
      ? p.detections.length
      : undefined;
  if (typeof detCount === "number") {
    parts.push(`${detCount} ta aniqlash`);
  }

  const model =
    (typeof p.model_name === "string" && p.model_name) ||
    (typeof p.model === "string" && p.model) ||
    "";
  const version = typeof p.model_version === "string" ? p.model_version : "";
  if (model) parts.push(version ? `${model} ${version}` : model);

  if (typeof p.overall_risk === "string" && p.overall_risk) {
    parts.push(`xavf: ${p.overall_risk}`);
  }

  if (typeof p.outcome === "string" && p.outcome) {
    parts.push(`qaror: ${p.outcome}`);
  }

  if (typeof p.reason === "string" && p.reason) {
    parts.push(p.reason);
  }

  if (parts.length === 0) {
    return EVENT_TYPE_UZ[e.event_type] ?? e.event_type;
  }
  return parts.join(" · ");
}

export function AuditLog({ entries, chainValid }: Props) {
  if (entries.length === 0) {
    return (
      <div
        style={{
          textAlign: "center",
          color: "#5b6679",
          fontSize: 12.5,
          padding: "24px 0",
        }}
      >
        {AUDIT_EMPTY}
      </div>
    );
  }

  return (
    <div role="list" aria-label="Audit jurnali">
      {entries.map((e, i) => {
        const color = entryColor(e);
        const isLast = i === entries.length - 1;
        const time = new Date(e.created_at).toLocaleTimeString("uz-Latn-UZ", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
        return (
          <div key={e.event_id} role="listitem" style={{ display: "flex", gap: 13 }}>
            {/* left rail */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <span
                aria-hidden="true"
                style={{
                  width: 11,
                  height: 11,
                  borderRadius: 999,
                  background: color,
                  boxShadow: `0 0 0 3px ${hexA(color, 0.18)}`,
                  flex: "none",
                  marginTop: 3,
                }}
              />
              {!isLast && (
                <span
                  aria-hidden="true"
                  style={{
                    flex: 1,
                    width: 2,
                    background: "rgba(255,255,255,0.08)",
                    marginTop: 5,
                  }}
                />
              )}
            </div>

            {/* body */}
            <div style={{ flex: 1, paddingBottom: 20 }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                }}
              >
                <span
                  className="font-mono"
                  style={{ fontSize: 12, fontWeight: 600, color: "#cbd5e1" }}
                >
                  {EVENT_TYPE_UZ[e.event_type] ?? e.event_type}
                </span>
                <span className="font-mono" style={{ fontSize: 11, color: "#7c8aa3" }}>
                  {time}
                </span>
              </div>

              <div
                style={{
                  fontSize: 12.5,
                  color: "#94a3b8",
                  margin: "5px 0 7px",
                  lineHeight: 1.5,
                }}
              >
                {entrySummary(e)}
              </div>

              <div style={{ display: "flex", gap: 11, alignItems: "center" }}>
                <span style={{ fontSize: 10.5, color: "#7c8aa3" }}>
                  operator:{" "}
                  <span className="font-mono" style={{ color: "#aebbcf" }}>
                    {e.operator_id ?? "system"}
                  </span>
                </span>
                <span
                  style={{
                    display: "inline-flex",
                    gap: 5,
                    alignItems: "center",
                    fontSize: 10.5,
                    color: "#22c55e",
                  }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: 999,
                      background: "#22c55e",
                    }}
                  />
                  HMAC tasdiqlangan
                </span>
              </div>
            </div>
          </div>
        );
      })}

      {chainValid !== false ? (
        <div
          role="status"
          style={{
            marginTop: 6,
            padding: 11,
            borderRadius: 10,
            background: "rgba(34,197,94,0.06)",
            border: "1px solid rgba(34,197,94,0.2)",
            fontSize: 11.5,
            color: "#86efac",
            lineHeight: 1.5,
          }}
        >
          Zanjir yaxlitligi tasdiqlandi · barcha yozuvlar qo'shimcha-yozuvli
          (append-only) jurnalda.
        </div>
      ) : (
        <div
          role="status"
          style={{
            marginTop: 6,
            padding: 11,
            borderRadius: 10,
            background: "rgba(239,68,68,0.08)",
            border: "1px solid rgba(239,68,68,0.3)",
            fontSize: 11.5,
            color: "#fca5a5",
            lineHeight: 1.5,
          }}
        >
          {AUDIT_TAMPERED}
        </div>
      )}
    </div>
  );
}
