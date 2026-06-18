import { useState } from "react";
import { RefreshCw, Inbox, Camera, Loader2 } from "lucide-react";
import type { ScanRecord } from "../lib/types";
import { captureCamera, ApiError } from "../lib/api";
import { ScanStatus } from "./ScanStatus";
import {
  QUEUE_TITLE, QUEUE_EMPTY, QUEUE_REFRESH,
  QUEUE_FILTER_ALL, QUEUE_FILTER_OPEN, QUEUE_FILTER_DONE,
  CAPTURE_BUTTON, CAPTURE_WORKING, CAPTURE_ERROR,
  SCAN_SUBJECT,
} from "../lib/uz";

type Filter = "all" | "open" | "done";

const OPEN_STATES  = new Set(["pending", "analyzing", "analyzed", "verdicted", "reviewing"]);
const DONE_STATES  = new Set(["decided", "error"]);

const RISK_DOT: Record<string, string> = {
  high:   "bg-red-500",
  medium: "bg-amber-500",
  low:    "bg-blue-500",
  clear:  "bg-green-500",
};

interface Props {
  scans:      ScanRecord[];
  loading:    boolean;
  selectedId: string | null;
  onSelect:   (id: string) => void;
  onRefresh:  () => void;
}

export function ScanQueue({ scans, loading, selectedId, onSelect, onRefresh }: Props) {
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
    <aside className="flex flex-col h-full border-r border-surface-border w-64 shrink-0">
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
          className="w-full flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-semibold bg-blue-700 hover:bg-blue-600 disabled:bg-surface-border disabled:text-content-muted text-white transition-colors"
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

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {visible.length === 0 && (
          <div className="flex flex-col items-center justify-center h-32 gap-2 text-content-muted">
            <Inbox size={20} aria-hidden="true" />
            <p className="text-xs">{QUEUE_EMPTY}</p>
          </div>
        )}

        {visible.map((s) => {
          const isSelected = s.scan_id === selectedId;
          const time = new Date(s.acquired_at).toLocaleTimeString("uz-Latn-UZ", {
            hour: "2-digit", minute: "2-digit",
          });

          return (
            <button
              key={s.scan_id}
              onClick={() => onSelect(s.scan_id)}
              aria-pressed={isSelected}
              aria-label={`${SCAN_SUBJECT[s.subject]} — ${s.state}`}
              className={`w-full flex flex-col gap-1 px-3 py-2.5 border-b border-surface-border text-left transition-colors ${
                isSelected
                  ? "bg-surface-hover border-l-2 border-l-blue-500"
                  : "hover:bg-surface-hover/50"
              }`}
            >
              {/* Row 1: subject + risk dot + time */}
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-content-primary truncate">
                  {SCAN_SUBJECT[s.subject]}
                  {s.lane_id && (
                    <span className="ml-1.5 text-content-muted font-normal">· {s.lane_id}</span>
                  )}
                </span>
                <div className="flex items-center gap-1.5 shrink-0">
                  {s.overall_risk && s.overall_risk !== "clear" && (
                    <span
                      className={`w-2 h-2 rounded-full ${RISK_DOT[s.overall_risk]}`}
                      aria-hidden="true"
                    />
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

