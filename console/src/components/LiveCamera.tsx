import { useEffect, useState } from "react";
import {
  Video, VideoOff, Play, Square, Loader2, AlertTriangle, Info,
  CheckCircle2, ShieldAlert,
} from "lucide-react";
import type { CameraRiskBand, WsCameraAnalysis, WsMessage } from "../lib/types";
import {
  cameraLiveUrl, startCameraStream, stopCameraStream, getCameraStreamStatus,
  type CameraStreamStatus, ApiError,
} from "../lib/api";
import { useWebSocket } from "../hooks/useWebSocket";
import { IS_MOCK } from "../lib/mock";
import {
  LIVE_TITLE, LIVE_START, LIVE_STOP, LIVE_STARTING, LIVE_STOPPING,
  LIVE_RUNNING, LIVE_STOPPED, LIVE_NO_SIGNAL, LIVE_DEVICE, LIVE_CADENCE,
  LIVE_FRAMES, LIVE_LAST_ANALYSIS, LIVE_ANALYSIS_TITLE, LIVE_NO_ANALYSIS,
  LIVE_DETECTIONS, LIVE_ERROR, CAMERA_RISK_SHORT, THREAT_CATEGORY,
} from "../lib/uz";

const MAX_FEED = 8;

// Risk styling — colour + icon + text together (never colour-only).
// "unavailable" (detector/VLM unwired) is a distinct neutral/warning state,
// never styled like "clear" — the operator must not read it as a clearance.
const RISK_UI: Record<CameraRiskBand, { cls: string; icon: React.ReactNode }> = {
  high:   { cls: "bg-risk-high-bg border-risk-high-border text-risk-high-text",     icon: <ShieldAlert size={14} aria-hidden="true" /> },
  medium: { cls: "bg-risk-medium-bg border-risk-medium-border text-risk-medium-text", icon: <AlertTriangle size={14} aria-hidden="true" /> },
  low:    { cls: "bg-risk-low-bg border-risk-low-border text-risk-low-text",          icon: <Info size={14} aria-hidden="true" /> },
  clear:  { cls: "bg-risk-clear-bg border-risk-clear-border text-risk-clear-text",    icon: <CheckCircle2 size={14} aria-hidden="true" /> },
  unavailable: { cls: "bg-surface-card border-surface-border text-content-secondary", icon: <AlertTriangle size={14} aria-hidden="true" /> },
};

interface AnalysisItem extends WsCameraAnalysis {
  _id: string;
}

export function LiveCamera() {
  const [status, setStatus]     = useState<CameraStreamStatus | null>(null);
  const [busy, setBusy]         = useState<"start" | "stop" | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const [feed, setFeed]         = useState<AnalysisItem[]>([]);
  const [imgError, setImgError] = useState(false);

  const running = status?.running ?? false;

  // Poll status once on mount (and reflect server truth).
  useEffect(() => {
    if (IS_MOCK) return;
    let cancelled = false;
    void getCameraStreamStatus()
      .then((s) => { if (!cancelled) setStatus(s); })
      .catch(() => { /* status unknown — keep null */ });
    return () => { cancelled = true; };
  }, []);

  // Continuous camera.analysis messages.
  useWebSocket((msg: WsMessage) => {
    if (msg.type !== "camera.analysis") return;
    const item: AnalysisItem = { ...msg, _id: `${msg.ts}-${msg.device}` };
    setFeed((prev) => [item, ...prev].slice(0, MAX_FEED));
  });

  const handleStart = async () => {
    setBusy("start");
    setError(null);
    try {
      const s = await startCameraStream();
      setStatus(s);
      setImgError(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : LIVE_ERROR);
    } finally {
      setBusy(null);
    }
  };

  const handleStop = async () => {
    setBusy("stop");
    setError(null);
    try {
      const s = await stopCameraStream();
      setStatus(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : LIVE_ERROR);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section
      aria-labelledby="live-heading"
      className="rounded-xl border border-white/10 glass overflow-hidden shadow-elev-3"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-surface-border">
        <Video size={16} className="text-blue-400" aria-hidden="true" />
        <h2 id="live-heading" className="text-sm font-semibold text-content-primary">
          {LIVE_TITLE}
        </h2>
        <span
          className={`ml-2 inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
            running
              ? "bg-green-900/40 text-green-300 border border-green-800/60"
              : "bg-surface-border text-content-muted"
          }`}
          role="status"
        >
          {running ? <Video size={11} aria-hidden="true" /> : <VideoOff size={11} aria-hidden="true" />}
          {running ? LIVE_RUNNING : LIVE_STOPPED}
        </span>

        <div className="ml-auto flex items-center gap-2">
          {!running ? (
            <button
              onClick={handleStart}
              disabled={busy !== null || IS_MOCK}
              className="press flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-sm font-semibold bg-gradient-to-b from-blue-600 to-blue-700 hover:from-blue-500 hover:to-blue-600 disabled:opacity-50 text-white shadow-elev-2 transition-all"
              aria-busy={busy === "start"}
            >
              {busy === "start"
                ? <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                : <Play size={13} aria-hidden="true" />}
              {busy === "start" ? LIVE_STARTING : LIVE_START}
            </button>
          ) : (
            <button
              onClick={handleStop}
              disabled={busy !== null}
              className="press flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-sm font-semibold bg-gradient-to-b from-red-700 to-red-800 hover:from-red-600 hover:to-red-700 disabled:opacity-50 text-white shadow-elev-2 transition-all"
              aria-busy={busy === "stop"}
            >
              {busy === "stop"
                ? <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                : <Square size={13} aria-hidden="true" />}
              {busy === "stop" ? LIVE_STOPPING : LIVE_STOP}
            </button>
          )}
        </div>
      </div>

      {error && (
        <p className="px-3 py-1.5 text-sm text-red-300 bg-red-900/30" role="alert">{error}</p>
      )}

      <div className="flex flex-col lg:flex-row">
        {/* Live preview */}
        <div className="relative lg:w-1/2 aspect-video bg-black flex items-center justify-center surface-sunken m-3 rounded-xl overflow-hidden">
          <div className="pointer-events-none absolute inset-0 z-10 rounded-xl" style={{ boxShadow: "inset 0 0 50px 10px rgba(0,0,0,0.7)" }} aria-hidden="true" />
          {running && !imgError ? (
            <img
              src={cameraLiveUrl()}
              alt={LIVE_TITLE}
              className="w-full h-full object-contain"
              onError={() => setImgError(true)}
            />
          ) : (
            <div className="flex flex-col items-center gap-2 text-content-muted">
              <VideoOff size={32} className="opacity-40" aria-hidden="true" />
              <span className="text-sm">{LIVE_NO_SIGNAL}</span>
            </div>
          )}
        </div>

        {/* Status + analysis feed */}
        <div className="lg:w-1/2 p-3 flex flex-col gap-3 min-w-0">
          {/* Stream stats */}
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
            <dt className="text-content-muted">{LIVE_DEVICE}</dt>
            <dd className="text-content-secondary font-mono truncate">{status?.device ?? "—"}</dd>
            <dt className="text-content-muted">{LIVE_CADENCE}</dt>
            <dd className="text-content-secondary font-mono">
              {status?.cadence_s != null ? `${status.cadence_s}s` : "—"}
            </dd>
            <dt className="text-content-muted">{LIVE_FRAMES}</dt>
            <dd className="text-content-secondary font-mono">{status?.frames_analyzed ?? 0}</dd>
            <dt className="text-content-muted">{LIVE_LAST_ANALYSIS}</dt>
            <dd className="text-content-secondary font-mono">
              {status?.last_analysis_ts
                ? new Date(status.last_analysis_ts).toLocaleTimeString("uz-Latn-UZ")
                : "—"}
            </dd>
          </dl>

          {/* Analysis feed */}
          <div className="min-w-0">
            <h3 className="text-xs font-semibold text-content-secondary uppercase tracking-wide mb-1.5">
              {LIVE_ANALYSIS_TITLE}
            </h3>

            {feed.length === 0 && (
              <p className="text-sm text-content-muted">{LIVE_NO_ANALYSIS}</p>
            )}

            <ul className="space-y-1.5">
              {feed.map((a) => {
                const ui = RISK_UI[a.risk_band];
                const top = [...a.detections]
                  .sort((x, y) => y.score - x.score)
                  .slice(0, 3);
                return (
                  <li
                    key={a._id}
                    className={`rounded-lg border p-2 text-sm animate-rise-in shadow-elev-2 ${ui.cls} ${
                      a.risk_band === "high" ? "halo-high" : ""
                    }`}
                  >
                    <div className="flex items-center gap-1.5 font-semibold">
                      {ui.icon}
                      <span>{CAMERA_RISK_SHORT[a.risk_band]}</span>
                      <span className="ml-auto font-mono text-xs opacity-80">
                        {new Date(a.ts).toLocaleTimeString("uz-Latn-UZ")}
                      </span>
                    </div>
                    {a.summary_uz && (
                      <p className="mt-0.5 text-content-secondary leading-snug">{a.summary_uz}</p>
                    )}
                    <p className="mt-0.5 text-xs text-content-muted">
                      {LIVE_DETECTIONS}: {a.n_detections}
                    </p>
                    {top.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {top.map((d, i) => (
                          <span
                            key={i}
                            className="px-1.5 py-0.5 rounded bg-black/30 text-xs font-medium"
                          >
                            {THREAT_CATEGORY[d.category]} {Math.round(d.score * 100)}%
                          </span>
                        ))}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}
