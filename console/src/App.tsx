import { useState, useEffect, useCallback, useRef } from "react";
import { ScanLine, Bell, CheckCircle2, Loader2 } from "lucide-react";
import type {
  AuthState, ScanRecord, DetectionJudgement, ThreatCategory,
  OperatorAnnotation, WsMessage,
} from "./lib/types";
import {
  getScanAudit, markReviewing, loadToken, clearToken,
  AUTH_EXPIRED_EVENT, type AuditEntry,
} from "./lib/api";
import { IS_MOCK } from "./lib/mock";
import { useScanQueue } from "./hooks/useScanQueue";
import { useScan } from "./hooks/useScan";
import {
  WebSocketProvider, useWebSocket,
} from "./hooks/useWebSocket";
import { ScanQueue } from "./components/ScanQueue";
import { VerdictPanel } from "./components/VerdictPanel";
import { DecisionPanel } from "./components/DecisionPanel";
import { AuditLog } from "./components/AuditLog";
import { ScanStatus } from "./components/ScanStatus";
import { LoginScreen } from "./components/LoginScreen";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { HighRiskBanner, type HighRiskAlert } from "./components/HighRiskBanner";
import { LiveCamera } from "./components/LiveCamera";
import {
  APP_TITLE, LANE_LABEL, OPERATOR_LABEL, LOADING, LOGOUT,
  AUDIT_TITLE, SCAN_LOAD_ERROR, RETRY,
  MARK_REVIEWED, MARK_REVIEWED_DONE,
} from "./lib/uz";

// Dev/demo bypass — ONLY when explicitly enabled via VITE_AUTH_BYPASS (or mock
// mode). It is NOT the default; production builds require a real login.
const AUTH_BYPASS = import.meta.env.VITE_AUTH_BYPASS === "true" || IS_MOCK;

const BYPASS_AUTH: AuthState = {
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

// ==================================================================
// Root: owns auth + provides the single shared WebSocket connection.
// ==================================================================
export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(() => {
    if (AUTH_BYPASS) return BYPASS_AUTH;
    return loadToken() ? { ...BYPASS_AUTH, token: loadToken()! } : null;
  });

  // Session expiry (401) → drop auth and show login, WITHOUT a page reload loop.
  useEffect(() => {
    const onExpired = () => { if (!AUTH_BYPASS) setAuth(null); };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  if (!auth) {
    return <LoginScreen onLogin={setAuth} />;
  }

  const laneId = auth.laneIds[0] ?? null;

  const handleLogout = () => {
    clearToken();
    if (!AUTH_BYPASS) setAuth(null);
  };

  return (
    <WebSocketProvider laneId={laneId}>
      <Console auth={auth} onLogout={handleLogout} />
    </WebSocketProvider>
  );
}

// ==================================================================
// Console: the operator workspace.
// ==================================================================
function Console({ auth, onLogout }: { auth: AuthState; onLogout: () => void }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [judgements, setJudgements] = useState<Record<string, JudgementEntry>>({});
  const [annotations, setAnnotations] = useState<OperatorAnnotation[]>([]);
  const [showAudit, setShowAudit] = useState(false);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [auditChainValid, setAuditChainValid] = useState<boolean | null>(null);
  const [highAlert, setHighAlert] = useState<HighRiskAlert | null>(null);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [reviewing, setReviewing] = useState(false);
  const [srAlert, setSrAlert] = useState("");

  const mainRef = useRef<HTMLElement>(null);
  const srTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const laneId = auth.laneIds[0] ?? null;

  // Queue + selected scan
  const { scans, loading: qLoading, error: qError, refresh, upsert } = useScanQueue(laneId);
  const { scan, setScan, loading: sLoading, error: sError, refresh: refreshScan } = useScan(selectedId);

  // ----------------------------------------------------------------
  // Select a scan → reset per-scan state and move focus to the main panel.
  // ----------------------------------------------------------------
  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
    setJudgements({});
    setAnnotations([]);
    setShowAudit(false);
  }, []);

  // Move keyboard focus to the main panel once a scan is loaded.
  useEffect(() => {
    if (scan && !sLoading) mainRef.current?.focus();
  }, [scan, sLoading]);

  // Transient screen-reader announcement with cleaned-up timer.
  const announce = useCallback((text: string) => {
    setSrAlert(text);
    if (srTimerRef.current) clearTimeout(srTimerRef.current);
    srTimerRef.current = setTimeout(() => setSrAlert(""), 4000);
  }, []);
  useEffect(() => () => { if (srTimerRef.current) clearTimeout(srTimerRef.current); }, []);

  // ----------------------------------------------------------------
  // WebSocket — canonical dotted message types + risk_band.
  // (Queue refresh on these events is handled inside useScanQueue.)
  // ----------------------------------------------------------------
  useWebSocket((msg: WsMessage) => {
    if (msg.type === "scan.flagged" && msg.risk_band === "high") {
      setHighAlert({ scanId: msg.scan_id, riskBand: msg.risk_band, ts: msg.ts });
      announce("Diqqat: yuqori xavf darajasi aniqlandi");
    }
    // If the open scan changes state, reload it.
    if (
      "scan_id" in msg &&
      msg.scan_id === selectedId &&
      (msg.type === "scan.analyzed" || msg.type === "scan.flagged" || msg.type === "scan.decided")
    ) {
      refreshScan();
    }
  });

  // ----------------------------------------------------------------
  // Decision callback (from DecisionPanel — the single decision path)
  // ----------------------------------------------------------------
  const handleDecided = useCallback((updated: ScanRecord) => {
    setScan(updated);
    upsert(updated);
    setJudgements({});
    setAnnotations([]);
    announce("Qaror jurnalga yozildi");
  }, [setScan, upsert, announce]);

  // ----------------------------------------------------------------
  // Non-decision action: "mark as reviewed" (does NOT clear/seize — auditing
  // and the real outcome stay solely with the DecisionPanel).
  // ----------------------------------------------------------------
  const handleMarkReviewed = useCallback(async () => {
    if (!scan) return;
    setReviewing(true);
    try {
      if (!IS_MOCK) await markReviewing(scan.scan_id);
      const updated: ScanRecord = { ...scan, state: "reviewing" };
      setScan(updated);
      upsert(updated);
      announce(MARK_REVIEWED_DONE);
    } catch {
      /* surfaced via queue/scan errors; non-blocking action */
    } finally {
      setReviewing(false);
    }
  }, [scan, setScan, upsert, announce]);

  // ----------------------------------------------------------------
  // Audit
  // ----------------------------------------------------------------
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

  const handleOpenHighAlert = useCallback((id: string) => {
    handleSelect(id);
    setHighAlert(null);
  }, [handleSelect]);

  // ----------------------------------------------------------------
  return (
    <div className="flex flex-col h-screen bg-surface text-content-primary overflow-hidden">

      {/* ── Top bar ── */}
      <header className="flex items-center gap-3 px-4 py-2 border-b border-surface-border bg-surface-card shrink-0">
        <ScanLine size={18} className="text-blue-400" aria-hidden="true" />
        <span className="text-sm font-bold tracking-tight">{APP_TITLE}</span>

        {laneId && (
          <span className="text-sm text-content-muted">
            {LANE_LABEL}: <span className="font-medium text-content-secondary">{laneId}</span>
          </span>
        )}

        <div className="ml-auto flex items-center gap-3">
          <ConnectionStatus />
          <span className="text-sm text-content-muted">
            {OPERATOR_LABEL}: <span className="font-medium text-content-secondary">{auth.username}</span>
          </span>
          <Clock />
          <button
            onClick={onLogout}
            className="text-sm text-content-muted hover:text-content-primary transition-colors"
          >
            {LOGOUT}
          </button>
        </div>
      </header>

      {/* ── Persistent high-risk banner (stays until operator acts) ── */}
      {highAlert && (
        <HighRiskBanner
          alert={highAlert}
          soundEnabled={soundEnabled}
          onToggleSound={() => setSoundEnabled((s) => !s)}
          onOpen={handleOpenHighAlert}
          onDismiss={() => setHighAlert(null)}
        />
      )}
      <span className="sr-only" role="status" aria-live="assertive" aria-atomic="true">{srAlert}</span>

      {/* ── Body: queue | main ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Left: Scan queue */}
        <ScanQueue
          scans={scans}
          loading={qLoading}
          error={qError}
          selectedId={selectedId}
          onSelect={handleSelect}
          onRefresh={refresh}
        />

        {/* Center + right */}
        <main
          ref={mainRef}
          tabIndex={-1}
          className="flex-1 flex flex-col min-w-0 overflow-y-auto focus:outline-none"
        >
          {/* Live camera — always available above the scan workspace */}
          <div className="p-4 pb-0">
            <LiveCamera />
          </div>

          {!scan && !sLoading && !sError && <EmptyState />}

          {sError && !sLoading && (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 text-content-muted">
              <p className="text-sm text-red-400" role="alert">{SCAN_LOAD_ERROR}</p>
              <button
                onClick={refreshScan}
                className="px-3 py-1.5 rounded text-sm font-medium border border-surface-border hover:bg-surface-hover"
              >
                {RETRY}
              </button>
            </div>
          )}

          {sLoading && (
            <div className="flex-1 flex items-center justify-center text-content-muted text-sm animate-pulse">
              {LOADING}
            </div>
          )}

          {scan && !sLoading && (
            <div className="flex-1 flex flex-col min-h-0 p-4 gap-4">
              {/* Scan header */}
              <div className="flex items-center gap-3 shrink-0 flex-wrap">
                <ScanStatus state={scan.state} risk={scan.overall_risk} />
                <span className="text-sm text-content-muted font-mono">
                  {new Date(scan.acquired_at).toLocaleString("uz-Latn-UZ")}
                </span>
                <div className="ml-auto flex items-center gap-2">
                  {/* Non-decision action only — the real outcome lives in the
                      DecisionPanel (single decision path, no conflicting buttons). */}
                  {scan.state !== "decided" && scan.state !== "reviewing" && (
                    <button
                      onClick={handleMarkReviewed}
                      disabled={reviewing}
                      className="flex items-center gap-1.5 px-2.5 py-1 rounded text-sm font-medium border border-surface-border text-content-secondary hover:bg-surface-hover disabled:opacity-50 transition-colors"
                    >
                      {reviewing
                        ? <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                        : <CheckCircle2 size={13} aria-hidden="true" />}
                      {MARK_REVIEWED}
                    </button>
                  )}
                  <button
                    onClick={toggleAudit}
                    aria-pressed={showAudit}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-sm font-medium transition-colors ${
                      showAudit
                        ? "bg-blue-700/40 text-blue-300 border border-blue-700"
                        : "border border-surface-border text-content-secondary hover:bg-surface-hover"
                    }`}
                  >
                    <Bell size={13} aria-hidden="true" />
                    {AUDIT_TITLE}
                  </button>
                </div>
              </div>

              {/* Main split: viewer+verdict | decision */}
              <div className="flex flex-1 gap-4 min-h-0 overflow-hidden">
                <div className="flex-1 min-w-0 overflow-y-auto">
                  <VerdictPanel
                    scan={scan}
                    judgements={judgements}
                    annotations={annotations}
                    onJudgementsChange={setJudgements}
                    onAnnotationsChange={setAnnotations}
                  />
                </div>

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
    <span className="text-sm font-mono text-content-muted tabular-nums w-16 text-right">
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
