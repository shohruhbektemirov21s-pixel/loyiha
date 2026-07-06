import { useState } from "react";
import {
  RefreshCw, ScanLine, Video, ImageUp,
  ShieldAlert, AlertTriangle, Info, Loader2,
} from "lucide-react";
import type { RiskBand, ScanRecord, ScanState } from "../lib/types";
import { captureCamera, ApiError } from "../lib/api";
import { BAND, BAND_ANALYZING, BAND_FAILED, hexA, ACCENT } from "../lib/theme";
import {
  QUEUE_REFRESH,
  QUEUE_FILTER_ALL, QUEUE_FILTER_OPEN, QUEUE_FILTER_DONE,
  CAPTURE_WORKING, CAPTURE_ERROR,
  SCAN_SUBJECT, RISK_BAND_SHORT, QUEUE_LOAD_ERROR, RETRY, QUEUE_EMPTY,
} from "../lib/uz";

type Filter = "all" | "open" | "done";

const OPEN_STATES = new Set(["pending", "analyzing", "analyzed", "verdicted", "reviewing"]);
const DONE_STATES = new Set(["decided", "error"]);

// Resolve a scan to its display band token + short label. `analyzing` and
// `error` are surfaced as fail-safe pseudo-bands (never collapsed to "clear").
function bandFor(s: ScanRecord): { color: string; bg: string; short: string } {
  if (s.state === "analyzing" || s.state === "pending")
    return { ...BAND_ANALYZING, short: "Tahlil…" };
  if (s.state === "error")
    return { ...BAND_FAILED, short: "Xato" };
  const b: RiskBand = s.overall_risk ?? "clear";
  return { ...BAND[b], short: RISK_BAND_SHORT[b] };
}

function bandIcon(short: string, color: string) {
  if (short === RISK_BAND_SHORT.high)
    return <ShieldAlert size={11} color={color} aria-hidden="true" />;
  if (short === RISK_BAND_SHORT.medium || short === "Xato")
    return <AlertTriangle size={11} color={color} aria-hidden="true" />;
  if (short === RISK_BAND_SHORT.low)
    return <Info size={11} color={color} aria-hidden="true" />;
  return <span style={{ width: 6, height: 6, borderRadius: 999, background: color }} aria-hidden="true" />;
}

interface Props {
  scans:        ScanRecord[];
  loading:      boolean;
  error?:       string | null;
  selectedId:   string | null;
  onSelect:     (id: string) => void;
  onRefresh:    () => void;
  acqMode:      "camera" | "upload";
  onModeChange: (m: "camera" | "upload") => void;
  // When false (e.g. the cloud deploy has no USB camera) the live-camera
  // capture button and the camera/upload toggle are hidden — upload only.
  cameraEnabled?: boolean;
}

export function ScanQueue({
  scans, loading, error, selectedId, onSelect, onRefresh, acqMode, onModeChange,
  cameraEnabled = true,
}: Props) {
  const [filter, setFilter] = useState<Filter>("open");
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);

  const handleCapture = async () => {
    setCapturing(true);
    setCaptureError(null);
    try {
      const res = await captureCamera();
      onRefresh();
      onSelect(res.scan_id);
    } catch (e) {
      setCaptureError(e instanceof ApiError ? e.message : CAPTURE_ERROR);
    } finally {
      setCapturing(false);
    }
  };

  const counts = {
    open: scans.filter((s) => OPEN_STATES.has(s.state)).length,
    all:  scans.length,
    done: scans.filter((s) => DONE_STATES.has(s.state)).length,
  };

  const visible = scans
    .filter((s) =>
      filter === "all" ? true : filter === "open" ? OPEN_STATES.has(s.state) : DONE_STATES.has(s.state),
    )
    // High-risk, undecided scans float to the top.
    .slice()
    .sort((a, b) => {
      const r = (x: ScanRecord) =>
        x.overall_risk === "high" && x.state !== "decided" ? 0 : 1;
      return r(a) - r(b);
    });

  const tabBtn = (active: boolean): React.CSSProperties => ({
    flex: 1, textAlign: "center", padding: "7px 6px", fontSize: 12, fontWeight: 600,
    borderRadius: 8, cursor: "pointer",
    border: `1px solid ${active ? "rgba(255,255,255,0.18)" : "transparent"}`,
    background: active ? "rgba(255,255,255,0.08)" : "transparent",
    color: active ? "#e2e8f0" : "#7c8aa3",
    transition: "all .15s",
  });

  const modeBtn = (active: boolean): React.CSSProperties => ({
    flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 7,
    padding: "9px 8px", fontSize: 12.5, fontWeight: 600, borderRadius: 9, cursor: "pointer",
    border: `1px solid ${active ? "rgba(20,184,166,0.5)" : "rgba(255,255,255,0.10)"}`,
    background: active ? "rgba(20,184,166,0.14)" : "rgba(255,255,255,0.03)",
    color: active ? "#2dd4bf" : "#8595ad",
    transition: "all .15s",
  });

  const filterDefs: [Filter, string, number][] = [
    ["open", QUEUE_FILTER_OPEN, counts.open],
    ["all", QUEUE_FILTER_ALL, counts.all],
    ["done", QUEUE_FILTER_DONE, counts.done],
  ];

  return (
    <aside
      className="flex flex-col h-full border-r border-white/10 shrink-0"
      style={{ width: 264, background: "rgba(255,255,255,0.015)" }}
    >
      {/* Header + actions */}
      <div className="flex flex-col gap-[11px] shrink-0" style={{ padding: "15px 14px 10px" }}>
        <div className="flex items-center justify-between">
          <span style={{ fontSize: 14, fontWeight: 700, letterSpacing: "-0.01em" }}>Navbat</span>
          <button
            onClick={onRefresh}
            aria-label={QUEUE_REFRESH}
            title={QUEUE_REFRESH}
            className="grid place-items-center"
            style={{
              width: 28, height: 28, borderRadius: 8, cursor: "pointer", color: "#8595ad",
              background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.10)",
            }}
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>

        {/* Take X-ray (camera capture) — only when a physical camera exists */}
        {cameraEnabled && (
          <button
            onClick={handleCapture}
            disabled={capturing}
            aria-busy={capturing}
            className="press"
            style={{
              width: "100%", padding: 11, border: "none", borderRadius: 10,
              fontSize: 13.5, fontWeight: 600, cursor: capturing ? "wait" : "pointer",
              color: "#052e2b", background: "linear-gradient(135deg,#2dd4bf,#0d9488)",
              boxShadow: "0 8px 20px rgba(20,184,166,0.3)",
              display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
              opacity: capturing ? 0.8 : 1,
            }}
          >
            {capturing
              ? <><Loader2 size={16} className="animate-spin" aria-hidden="true" />{CAPTURE_WORKING}</>
              : <><ScanLine size={16} aria-hidden="true" />Rentgen ko'rish</>}
          </button>
        )}
        {cameraEnabled && captureError && (
          <p style={{ fontSize: 12, color: "#fca5a5" }} role="alert">{captureError}</p>
        )}

        {/* Acquisition mode: camera ⇄ upload. Hidden when there is no camera —
            the only source is upload, so the toggle would be a dead control. */}
        {cameraEnabled && (
          <div role="tablist" aria-label="Tasvir manbasi"
            style={{ display: "flex", gap: 6, padding: 4, borderRadius: 11, background: "rgba(0,0,0,0.22)" }}>
            <div role="tab" aria-selected={acqMode === "camera"} onClick={() => onModeChange("camera")} style={modeBtn(acqMode === "camera")}>
              <Video size={15} aria-hidden="true" />Kamera
            </div>
            <div role="tab" aria-selected={acqMode === "upload"} onClick={() => onModeChange("upload")} style={modeBtn(acqMode === "upload")}>
              <ImageUp size={15} aria-hidden="true" />Rasm yuklash
            </div>
          </div>
        )}

        {/* Filter tabs with counts */}
        <div style={{ display: "flex", gap: 3 }}>
          {filterDefs.map(([f, label, n]) => (
            <div key={f} style={tabBtn(filter === f)} onClick={() => setFilter(f)}>
              {label} · {n}
            </div>
          ))}
        </div>
      </div>

      {/* Load error — visible, never silent */}
      {error && (
        <div className="mx-[11px] mb-2 rounded-lg" style={{ padding: "10px 12px", background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.4)" }} role="alert">
          <p style={{ fontSize: 13, color: "#fca5a5" }}>{QUEUE_LOAD_ERROR}</p>
          <button onClick={onRefresh} style={{ marginTop: 6, fontSize: 13, fontWeight: 600, color: "#fecaca", textDecoration: "underline", cursor: "pointer" }}>
            {RETRY}
          </button>
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto flex flex-col" style={{ padding: "4px 11px 16px", gap: 8 }}>
        {visible.length === 0 && !error && (
          <div style={{ textAlign: "center", padding: "40px 12px", color: "#5b6679", fontSize: 13 }}>
            {QUEUE_EMPTY}
          </div>
        )}

        {visible.map((s) => {
          const isSelected = s.scan_id === selectedId;
          const isHigh = s.overall_risk === "high" && s.state !== "decided";
          const decided = s.state === "decided";
          const time = new Date(s.acquired_at).toLocaleTimeString("uz-Latn-UZ", { hour: "2-digit", minute: "2-digit" });
          const bd = bandFor(s);

          const rowStyle: React.CSSProperties = {
            cursor: "pointer", padding: "10px 11px", borderRadius: 11,
            borderLeft: `3px solid ${isHigh ? "#ef4444" : isSelected ? ACCENT.indigo2 : "rgba(255,255,255,0.10)"}`,
            background: isSelected ? "rgba(99,102,241,0.10)" : isHigh ? "rgba(239,68,68,0.07)" : "rgba(255,255,255,0.025)",
            boxShadow: isSelected
              ? "0 0 0 1px rgba(129,140,248,0.55),0 10px 26px rgba(99,102,241,0.18)"
              : isHigh ? `0 6px 22px ${hexA("#ef4444", 0.16)}` : "none",
            transition: "all .15s",
          };

          return (
            <button
              key={s.scan_id}
              onClick={() => onSelect(s.scan_id)}
              aria-pressed={isSelected}
              aria-label={`${SCAN_SUBJECT[s.subject]} — ${stateWord(s.state)}` + (s.overall_risk ? ` — ${RISK_BAND_SHORT[s.overall_risk]} xavf` : "")}
              className={`text-left w-full ${isHigh && !isSelected ? "halo-high" : ""}`}
              style={rowStyle}
            >
              <div className="flex items-center justify-between" style={{ marginBottom: 7 }}>
                <span style={{ fontSize: 13.5, fontWeight: 600 }}>{SCAN_SUBJECT[s.subject]}</span>
                <span className="font-mono" style={{ fontSize: 11.5, color: "#7c8aa3" }}>{time}</span>
              </div>
              <div className="flex items-center flex-wrap" style={{ gap: 6, marginBottom: 7 }}>
                <span className="inline-flex items-center" style={{
                  gap: 5, fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 999,
                  color: bd.color, background: bd.bg, border: `1px solid ${hexA(bd.color, 0.35)}`,
                }}>
                  {bandIcon(bd.short, bd.color)}{bd.short}
                </span>
                {decided && (
                  <span style={{
                    fontSize: 10.5, fontWeight: 600, padding: "2px 7px", borderRadius: 999,
                    color: "#94a3b8", background: "rgba(148,163,184,0.12)", border: "1px solid rgba(148,163,184,0.25)",
                  }}>Qaror qilindi</span>
                )}
              </div>
              <div className="font-mono" style={{ fontSize: 11, color: "#5b6679" }}>{s.scan_id.slice(0, 8)}…</div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

function stateWord(s: ScanState): string {
  return s;
}
