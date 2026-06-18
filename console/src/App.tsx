import { useState, useEffect, useCallback } from "react";
import { ScanLine, Bell, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import type {
  AuthState, ScanRecord, DetectionJudgement, ThreatCategory,
  OperatorAnnotation, WsMessage,
} from "./lib/types";
import { getScanAudit, decideScan, ApiError, type AuditEntry } from "./lib/api";
import { IS_MOCK } from "./lib/mock";
import { useScanQueue } from "./hooks/useScanQueue";
import { useScan } from "./hooks/useScan";
import { useWebSocket } from "./hooks/useWebSocket";
import { ScanQueue } from "./components/ScanQueue";
import { VerdictPanel } from "./components/VerdictPanel";
import { DecisionPanel } from "./components/DecisionPanel";
import { AuditLog } from "./components/AuditLog";
import { ScanStatus } from "./components/ScanStatus";
import {
  APP_TITLE, LANE_LABEL, OPERATOR_LABEL, LOADING,
  AUDIT_TITLE, SR_RISK_HIGH,
  ARCHIVE_CONFIRM, ARCHIVE_REJECT, ARCHIVE_WORKING, ARCHIVE_ERROR, ARCHIVE_DONE,
} from "./lib/uz";

const DEFAULT_AUTH: AuthState = {
  token:      "bypass",
  operatorId: "e18dd952-0e93-4bef-8dbe-2694ccd6d66c",
  username:   "admin",
  role:       "admin",
  laneIds:    ["lane-1", "lane-2"],
};

// ------------------------------------------------------------------
interface JudgementEntry {
  judgement:  DetectionJudgement;
  corrected:  ThreatCategory | null;
}

// ------------------------------------------------------------------
export default function App() {
  const [auth]                  = useState<AuthState>(DEFAULT_AUTH);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [judgements, setJudgements] = useState<Record<string, JudgementEntry>>({});
  const [annotations, setAnnotations] = useState<OperatorAnnotation[]>([]);
  const [showAudit, setShowAudit] = useState(false);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [auditChainValid, setAuditChainValid] = useState<boolean | null>(null);
  const [liveAlert, setLiveAlert] = useState("");
  const [deciding, setDeciding] = useState<"confirmed" | "rejected" | null>(null);
  const [decideError, setDecideError] = useState<string | null>(null);

  const laneId = auth?.laneIds[0] ?? null;

  // ------------------------------------------------------------------
  // Queue
  // ------------------------------------------------------------------
  const { scans, loading: qLoading, refresh, upsert } = useScanQueue(laneId);

  // ------------------------------------------------------------------
  // Selected scan
  // ------------------------------------------------------------------
  const { scan, setScan, loading: sLoading } = useScan(selectedId);

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
    setJudgements({});
    setAnnotations([]);
    setShowAudit(false);
  }, []);

  // ------------------------------------------------------------------
  // WebSocket — new scan alert + queue refresh
  // ------------------------------------------------------------------
  useWebSocket((msg: WsMessage) => {
    if (msg.type === "scan_flagged" && msg.risk === "high") {
      setLiveAlert(SR_RISK_HIGH);
      setTimeout(() => setLiveAlert(""), 3000);
    }
    if (msg.type === "scan_decided" || msg.type === "scan_analyzed" || msg.type === "scan_flagged") {
      refresh();
    }
    // If the open scan changes state, refresh it
    if (
      "scan_id" in msg &&
      msg.scan_id === selectedId &&
      (msg.type === "scan_analyzed" || msg.type === "scan_flagged")
    ) {
      refresh();
    }
  }, laneId);

  // ------------------------------------------------------------------
  // Decision callback
  // ------------------------------------------------------------------
  const handleDecided = useCallback((updated: ScanRecord) => {
    setScan(updated);
    upsert(updated);
    setJudgements({});
    setAnnotations([]);
  }, [setScan, upsert]);

  // ------------------------------------------------------------------
  // Confirm / reject → archive
  // ------------------------------------------------------------------
  const handleDecide = useCallback(async (decision: "confirmed" | "rejected") => {
    if (!scan) return;
    setDeciding(decision);
    setDecideError(null);
    try {
      const res = await decideScan(scan.scan_id, decision);
      const updated: ScanRecord = { ...scan, state: "decided", decided_at: res.decided_at };
      setScan(updated);
      upsert(updated);
      setLiveAlert(ARCHIVE_DONE);
      setTimeout(() => setLiveAlert(""), 3000);
    } catch (e) {
      setDecideError(e instanceof ApiError ? e.message : ARCHIVE_ERROR);
    } finally {
      setDeciding(null);
    }
  }, [scan, setScan, upsert]);

  // ------------------------------------------------------------------
  // Audit
  // ------------------------------------------------------------------
  const toggleAudit = useCallback(async () => {
    if (!selectedId) return;
    if (showAudit) { setShowAudit(false); return; }
    if (!IS_MOCK) {
      try {
        const entries = await getScanAudit(selectedId);
        setAuditEntries(entries);
        setAuditChainValid(null);   // backend verifies; we just show
      } catch { setAuditEntries([]); }
    }
    setShowAudit(true);
  }, [selectedId, showAudit]);

  // ------------------------------------------------------------------
  // Main layout
  // ------------------------------------------------------------------
  return (
    <div className="flex flex-col h-screen bg-surface text-content-primary overflow-hidden">

      {/* ── Top bar ── */}
      <header className="flex items-center gap-3 px-4 py-2 border-b border-surface-border bg-surface-card shrink-0">
        <ScanLine size={18} className="text-blue-400" aria-hidden="true" />
        <span className="text-sm font-bold tracking-tight">{APP_TITLE}</span>

        {laneId && (
          <span className="text-xs text-content-muted">
            {LANE_LABEL}: <span className="font-medium text-content-secondary">{laneId}</span>
          </span>
        )}

        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-content-muted">
            {OPERATOR_LABEL}: <span className="font-medium text-content-secondary">{auth.username}</span>
          </span>
          {/* Clock */}
          <Clock />
        </div>
      </header>

      {/* ── Live announcement (screen reader + visible flash) ── */}
      {liveAlert && (
        <div
          role="alert"
          aria-live="assertive"
          className="px-4 py-1.5 bg-red-900/80 text-red-200 text-xs font-semibold text-center animate-fade-in"
        >
          {liveAlert}
        </div>
      )}
      <span className="sr-only" aria-live="polite" aria-atomic="true">{liveAlert}</span>

      {/* ── Body: queue | main | sidebar ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Left: Scan queue */}
        <ScanQueue
          scans={scans}
          loading={qLoading}
          selectedId={selectedId}
          onSelect={handleSelect}
          onRefresh={refresh}
        />

        {/* Center + right */}
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {!scan && !sLoading && (
            <EmptyState />
          )}

          {sLoading && (
            <div className="flex-1 flex items-center justify-center text-content-muted text-sm animate-pulse">
              {LOADING}
            </div>
          )}

          {scan && !sLoading && (
            <div className="flex-1 flex flex-col min-h-0 p-4 gap-4 overflow-hidden">
              {/* Scan header */}
              <div className="flex items-center gap-3 shrink-0 flex-wrap">
                <ScanStatus state={scan.state} risk={scan.overall_risk} />
                <span className="text-xs text-content-muted font-mono">
                  {new Date(scan.acquired_at).toLocaleString("uz-Latn-UZ")}
                </span>
                <div className="ml-auto flex items-center gap-2">
                  {scan.state !== "decided" && (
                    <>
                      <button
                        onClick={() => handleDecide("confirmed")}
                        disabled={deciding !== null}
                        className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-semibold bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white transition-colors"
                      >
                        {deciding === "confirmed"
                          ? <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                          : <CheckCircle2 size={12} aria-hidden="true" />}
                        {deciding === "confirmed" ? ARCHIVE_WORKING : ARCHIVE_CONFIRM}
                      </button>
                      <button
                        onClick={() => handleDecide("rejected")}
                        disabled={deciding !== null}
                        className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-semibold bg-red-800 hover:bg-red-700 disabled:opacity-50 text-white transition-colors"
                      >
                        {deciding === "rejected"
                          ? <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                          : <XCircle size={12} aria-hidden="true" />}
                        {deciding === "rejected" ? ARCHIVE_WORKING : ARCHIVE_REJECT}
                      </button>
                    </>
                  )}
                  <button
                    onClick={toggleAudit}
                    aria-pressed={showAudit}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                      showAudit
                        ? "bg-blue-700/40 text-blue-300 border border-blue-700"
                        : "border border-surface-border text-content-secondary hover:bg-surface-hover"
                    }`}
                  >
                    <Bell size={12} aria-hidden="true" />
                    {AUDIT_TITLE}
                  </button>
                </div>
                {decideError && (
                  <p className="w-full text-xs text-red-400" role="alert">{decideError}</p>
                )}
              </div>

              {/* Main split: viewer+verdict | decision */}
              <div className="flex flex-1 gap-4 min-h-0 overflow-hidden">
                {/* Verdict panel (viewer + detections) */}
                <div className="flex-1 min-w-0 overflow-y-auto">
                  <VerdictPanel
                    scan={scan}
                    judgements={judgements}
                    annotations={annotations}
                    onJudgementsChange={setJudgements}
                    onAnnotationsChange={setAnnotations}
                  />
                </div>

                {/* Right column: decision + audit */}
                <div className="w-80 shrink-0 flex flex-col gap-4 overflow-y-auto">
                  <DecisionPanel
                    scan={scan}
                    operatorId={auth.operatorId}
                    judgements={judgements}
                    annotations={annotations}
                    onDecided={handleDecided}
                  />

                  {showAudit && (
                    <div className="animate-slide-in">
                      <AuditLog
                        entries={IS_MOCK ? MOCK_AUDIT : auditEntries}
                        chainValid={auditChainValid}
                      />
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Clock
// ------------------------------------------------------------------
function Clock() {
  const [time, setTime] = useState(() => fmtTime());
  useEffect(() => {
    const id = setInterval(() => setTime(fmtTime()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="text-xs font-mono text-content-muted tabular-nums w-16 text-right">
      {time}
    </span>
  );
}
function fmtTime() {
  return new Date().toLocaleTimeString("uz-Latn-UZ", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ------------------------------------------------------------------
// Empty state
// ------------------------------------------------------------------
function EmptyState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3 text-content-muted">
      <ScanLine size={40} className="opacity-20" aria-hidden="true" />
      <p className="text-sm">Skan tanlang</p>
    </div>
  );
}

// ------------------------------------------------------------------
// Mock audit entries for dev mode
// ------------------------------------------------------------------
const MOCK_AUDIT: AuditEntry[] = [
  {
    event_id:    "evt-001",
    seq:         1,
    event_type:  "acquisition_recorded",
    operator_id: null,
    payload:     { scanner_id: "smiths-lane-1", modality: "dual_energy" },
    created_at:  new Date(Date.now() - 5 * 60_000).toISOString(),
    event_hmac:  "a".repeat(64),
  },
  {
    event_id:    "evt-002",
    seq:         2,
    event_type:  "detection_recorded",
    operator_id: null,
    payload:     { n_detections: 2, model: "YOLOv8-xray" },
    created_at:  new Date(Date.now() - 3 * 60_000).toISOString(),
    event_hmac:  "b".repeat(64),
  },
  {
    event_id:    "evt-003",
    seq:         3,
    event_type:  "verdict_recorded",
    operator_id: null,
    payload:     { overall_risk: "high", model: "Qwen3-VL" },
    created_at:  new Date(Date.now() - 1 * 60_000).toISOString(),
    event_hmac:  "c".repeat(64),
  },
];
