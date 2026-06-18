import { useState } from "react";
import {
  RefreshCw, Inbox, Camera, Loader2, AlertTriangle,
  ShieldAlert, Info,
} from "lucide-react";
import type { RiskBand, ScanRecord } from "../lib/types";
import { captureCamera, ApiError } from "../lib/api";
import { ScanStatus } from "./ScanStatus";
import {
  QUEUE_TITLE, QUEUE_EMPTY, QUEUE_REFRESH,
  QUEUE_FILTER_ALL, QUEUE_FILTER_OPEN, QUEUE_FILTER_DONE,
  CAPTURE_BUTTON, CAPTURE_WORKING, CAPTURE_ERROR,
  SCAN_SUBJECT, RISK_BAND_SHORT, QUEUE_LOAD_ERROR, RETRY,
} from "../lib/uz";

type Filter = "all" | "open" | "done";

const OPEN_STATES  = new Set(["pending", "analyzing", "analyzed", "verdicted", "reviewing"]);
const DONE_STATES  = new Set(["decided", "error"]);

// Risk badge: icon + text + colour (never colour-only — color-blind safe).
const RISK_BADGE: Record<RiskBand, { cls: string; icon: React.ReactNode }> = {
  high:   { cls: "bg-risk-high-bg text-risk-high-text border border-risk-high-border shadow-glow-high",       icon: <ShieldAlert size={11} aria-hidden="true" /> },
  medium: { cls: "bg-risk-medium-bg text-risk-medium-text border border-risk-medium-border", icon: <AlertTriangle size={11} aria-hidden="true" /> },
  low:    { cls: "bg-risk-low-bg text-risk-low-text border border-risk-low-border",           icon: <Info size={11} aria-hidden="true" /> },
  clear:  { cls: "bg-surface-border text-content-secondary",                                  icon: null },
};

interface Props {
  scans:      ScanRecord[];
  loading:    boolean;
  error?:     string | null;
  selectedId: string | null;
  onSelect:   (id: string) => void;
  onRefresh:  () => void;
}

export function ScanQueue({ scans, loading, error, selectedId, onSelect, onRefresh }: Props) {
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

  const visible = scans.filter((s) =>
    filter === "all"  ? true :
    filter === "open" ? OPEN_STATES.has(s.state) :
    DONE_STATES.has(s.state),
  );

  return (
    <aside className="flex flex-col h-full border-r border-white/10 glass-strong w-64 shrink-0 scene">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <h2 className="text-sm font-semibold text-content-primary">{QUEUE_TITLE}</h2>
        <button
          onClick={onRefresh}
          aria-label={QUEUE_REFRESH}
          title={QUEUE_REFRESH}
          className="p-1 rounded text-content-muted hover:text-content-primary transition-colors"
        >
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {/* Camera capture */}
      <div className="px-3 py-2 border-b border-surface-border">
        <button
          onClick={handleCapture}
          disabled={capturing}
          className="press w-full flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-semibold bg-gradient-to-b from-blue-600 to-blue-700 hover:from-blue-500 hover:to-blue-600 disabled:from-surface-border disabled:to-surface-border disabled:text-content-muted text-white shadow-elev-2 hover:shadow-glow-blue transition-all"
          aria-busy={capturing}
        >
          {capturing
            ? <><Loader2 size={13} className="animate-spin" aria-hidden="true" />{CAPTURE_WORKING}</>
            : <><Camera size={13} aria-hidden="true" />{CAPTURE_BUTTON}</>}
        </button>
        {captureError && (
          <p className="mt-1.5 text-xs text-red-400" role="alert">{captureError}</p>
        )}
      </div>

      {/* Filter tabs */}
      <div className="flex border-b border-surface-border">
        {([["open", QUEUE_FILTER_OPEN], ["all", QUEUE_FILTER_ALL], ["done", QUEUE_FILTER_DONE]] as [Filter, string][]).map(
          ([f, label]) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`flex-1 py-1.5 text-xs font-medium transition-colors ${
                filter === f
                  ? "text-content-primary border-b-2 border-blue-500"
                  : "text-content-muted hover:text-content-secondary"
              }`}
            >
              {label}
            </button>
          ),
        )}
      </div>

      {/* Load error — visible, never silent */}
      {error && (
        <div className="px-3 py-2 border-b border-red-800/60 bg-red-900/30" role="alert">
          <p className="text-sm text-red-300">{QUEUE_LOAD_ERROR}</p>
          <button
            onClick={onRefresh}
            className="mt-1.5 text-sm font-medium text-red-200 underline hover:text-white"
          >
            {RETRY}
          </button>
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {visible.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center h-32 gap-2 text-content-muted">
            <Inbox size={20} aria-hidden="true" />
            <p className="text-sm">{QUEUE_EMPTY}</p>
          </div>
        )}

        {visible.map((s) => {
          const isSelected = s.scan_id === selectedId;
          const isHigh     = s.overall_risk === "high";
          const time = new Date(s.acquired_at).toLocaleTimeString("uz-Latn-UZ", {
            hour: "2-digit", minute: "2-digit",
          });
          const badge = s.overall_risk ? RISK_BADGE[s.overall_risk] : null;

          return (
            <button
              key={s.scan_id}
              onClick={() => onSelect(s.scan_id)}
              aria-pressed={isSelected}
              aria-label={
                `${SCAN_SUBJECT[s.subject]} — ${s.state}` +
                (s.overall_risk ? ` — ${RISK_BAND_SHORT[s.overall_risk]} xavf` : "")
              }
              className={`tilt-soft relative w-full flex flex-col gap-1 px-3 py-2.5 border-b border-surface-border text-left ${
                isHigh ? "border-l-4 border-l-risk-high border-l-risk-high-border bg-risk-high-bg/40 halo-high z-10" : ""
              } ${
                isSelected
                  ? "bg-surface-hover border-l-4 border-l-blue-500 shadow-glow-blue"
                  : "hover:bg-surface-hover/60 hover:shadow-elev-2"
              }`}
            >
              {/* Row 1: subject + risk badge + time */}
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-content-primary truncate">
                  {SCAN_SUBJECT[s.subject]}
                  {s.lane_id && (
                    <span className="ml-1.5 text-content-muted font-normal">· {s.lane_id}</span>
                  )}
                </span>
                <div className="flex items-center gap-1.5 shrink-0">
                  {badge && s.overall_risk && s.overall_risk !== "clear" && (
                    <span
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold ${badge.cls}`}
                    >
                      {badge.icon}
                      {RISK_BAND_SHORT[s.overall_risk]}
                    </span>
                  )}
                  <span className="text-xs text-content-muted font-mono">{time}</span>
                </div>
              </div>

              {/* Row 2: state badge */}
              <ScanStatus state={s.state} risk={s.overall_risk} />

              {/* Row 3: scan ID */}
              <span className="text-xs text-content-muted font-mono">
                {s.scan_id.slice(0, 8)}…
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

