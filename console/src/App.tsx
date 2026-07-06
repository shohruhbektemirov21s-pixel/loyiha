import { useState, useEffect, useCallback, useRef } from "react";
import { ScanLine, X } from "lucide-react";
import type {
  AuthState, ScanRecord, DetectionJudgement, ThreatCategory,
  OperatorAnnotation, WsMessage,
} from "./lib/types";
import {
  getScanAudit, loadToken, clearToken, AUTH_EXPIRED_EVENT, type AuditEntry,
} from "./lib/api";
import { IS_MOCK } from "./lib/mock";
import { useScanQueue } from "./hooks/useScanQueue";
import { useScan } from "./hooks/useScan";
import { WebSocketProvider, useWebSocket } from "./hooks/useWebSocket";
import { ScanQueue } from "./components/ScanQueue";
import { VerdictPanel } from "./components/VerdictPanel";
import { DecisionPanel } from "./components/DecisionPanel";
import { AuditLog } from "./components/AuditLog";
import { LoginScreen } from "./components/LoginScreen";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { HighRiskBanner, type HighRiskAlert } from "./components/HighRiskBanner";
import { LiveCamera } from "./components/LiveCamera";
import { ImageScreening } from "./components/ImageScreening";
import {
  APP_TITLE, LANE_LABEL, OPERATOR_LABEL, LOADING, LOGOUT,
  AUDIT_TITLE, SCAN_LOAD_ERROR, RETRY,
} from "./lib/uz";

// Dev/demo bypass — ONLY when explicitly enabled via VITE_AUTH_BYPASS (or mock
// mode). It is NOT the default; production builds require a real login.
const AUTH_BYPASS = import.meta.env.VITE_AUTH_BYPASS === "true" || IS_MOCK;

// A physical USB camera exists on this deploy unless explicitly disabled. The
// cloud host has no camera, so its build sets VITE_ENABLE_CAMERA=false: the
// console then hides all camera capture UI and defaults to image upload.
const CAMERA_ENABLED = import.meta.env.VITE_ENABLE_CAMERA !== "false";

const BYPASS_AUTH: AuthState = {
  token:      "bypass",
  operatorId: "e18dd952-0e93-4bef-8dbe-2694ccd6d66c",
  username:   "admin",
  role:       "admin",
  laneIds:    ["lane-1", "lane-2"],
};

interface JudgementEntry {
  judgement: DetectionJudgement;
  corrected: ThreatCategory | null;
}

// ==================================================================
// Root: owns auth + provides the single shared WebSocket connection.
// ==================================================================
export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(() => {
    if (AUTH_BYPASS) return BYPASS_AUTH;
    return loadToken() ? { ...BYPASS_AUTH, token: loadToken()! } : null;
  });

  useEffect(() => {
    const onExpired = () => { if (!AUTH_BYPASS) setAuth(null); };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  if (!auth) return <LoginScreen onLogin={setAuth} />;

  const laneId = auth.laneIds[0] ?? null;
  const handleLogout = () => { clearToken(); if (!AUTH_BYPASS) setAuth(null); };

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
  const [selectedId, setSelectedId]   = useState<string | null>(null);
  const [judgements, setJudgements]   = useState<Record<string, JudgementEntry>>({});
  const [annotations, setAnnotations] = useState<OperatorAnnotation[]>([]);
  const [showAudit, setShowAudit]     = useState(false);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [auditChainValid, setAuditChainValid] = useState<boolean | null>(null);
  const [highAlert, setHighAlert]     = useState<HighRiskAlert | null>(null);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [srAlert, setSrAlert]         = useState("");
  const [acqMode, setAcqMode]         = useState<"camera" | "upload">(CAMERA_ENABLED ? "camera" : "upload");

  const mainRef    = useRef<HTMLElement>(null);
  const srTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const laneId = auth.laneIds[0] ?? null;

  const { scans, loading: qLoading, error: qError, refresh, upsert } = useScanQueue(laneId);
  const { scan, setScan, loading: sLoading, error: sError, refresh: refreshScan } = useScan(selectedId);

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
    setJudgements({});
    setAnnotations([]);
    setShowAudit(false);
    setAcqMode(CAMERA_ENABLED ? "camera" : "upload");
  }, []);

  useEffect(() => { if (scan && !sLoading) mainRef.current?.focus(); }, [scan, sLoading]);

  const announce = useCallback((text: string) => {
    setSrAlert(text);
    if (srTimerRef.current) clearTimeout(srTimerRef.current);
    srTimerRef.current = setTimeout(() => setSrAlert(""), 4000);
  }, []);
  useEffect(() => () => { if (srTimerRef.current) clearTimeout(srTimerRef.current); }, []);

  // WebSocket — canonical dotted message types + risk_band.
  useWebSocket((msg: WsMessage) => {
    if (msg.type === "scan.flagged" && msg.risk_band === "high") {
      setHighAlert({ scanId: msg.scan_id, riskBand: msg.risk_band, ts: msg.ts });
      announce("Diqqat: yuqori xavf darajasi aniqlandi");
    }
    if (
      "scan_id" in msg && msg.scan_id === selectedId &&
      (msg.type === "scan.analyzed" || msg.type === "scan.flagged" || msg.type === "scan.decided")
    ) {
      refreshScan();
    }
  });

  const handleDecided = useCallback((updated: ScanRecord) => {
    setScan(updated);
    upsert(updated);
    setJudgements({});
    setAnnotations([]);
    announce("Qaror jurnalga yozildi");
  }, [setScan, upsert, announce]);

  const toggleAudit = useCallback(async () => {
    if (!selectedId) return;
    if (showAudit) { setShowAudit(false); return; }
    if (!IS_MOCK) {
      try {
        const entries = await getScanAudit(selectedId);
        setAuditEntries(entries);
        setAuditChainValid(null);
      } catch { setAuditEntries([]); }
    }
    setShowAudit(true);
  }, [selectedId, showAudit]);

  const handleOpenHighAlert = useCallback((id: string) => {
    handleSelect(id);
    setHighAlert(null);
  }, [handleSelect]);

  const chromeBtn: React.CSSProperties = {
    fontSize: 12.5, fontWeight: 600, color: "#aebbcf", padding: "6px 12px", borderRadius: 8,
    cursor: "pointer", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.10)",
  };

  return (
    <div className="relative flex flex-col h-screen" style={{ minWidth: 1320, color: "#e2e8f0" }}>

      {/* ── Top chrome ── */}
      <header
        className="flex items-center justify-between shrink-0 border-b border-white/10 glass-strong"
        style={{ padding: "11px 18px" }}
      >
        <div className="flex items-center" style={{ gap: 13 }}>
          <span className="grid place-items-center shrink-0" style={{
            width: 38, height: 38, borderRadius: 10, background: "linear-gradient(135deg,#14b8a6,#0d9488)",
            boxShadow: "0 6px 16px rgba(20,184,166,0.35)",
          }} aria-hidden="true">
            <ScanLine size={20} color="#062a26" />
          </span>
          <div className="flex items-baseline" style={{ gap: 14 }}>
            <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: "-0.02em" }}>{APP_TITLE}</span>
            {laneId && (
              <span style={{ fontSize: 13, color: "#8595ad" }}>
                {LANE_LABEL}: <span style={{ color: "#cbd5e1", fontWeight: 600 }}>{laneId}</span>
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center" style={{ gap: 14 }}>
          <ConnectionStatus />
          <span style={{ fontSize: 13, color: "#8595ad" }}>
            {OPERATOR_LABEL}: <span style={{ color: "#e2e8f0", fontWeight: 600 }}>{auth.username}</span>
          </span>
          <Clock />
          <button onClick={toggleAudit} disabled={!selectedId} aria-pressed={showAudit}
            style={{ ...chromeBtn, opacity: selectedId ? 1 : 0.5, cursor: selectedId ? "pointer" : "not-allowed" }}>
            {AUDIT_TITLE}
          </button>
          <button onClick={onLogout}
            style={{ fontSize: 12.5, fontWeight: 600, color: "#fca5a5", padding: "6px 12px", borderRadius: 8, cursor: "pointer", background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.25)" }}>
            {LOGOUT}
          </button>
        </div>
      </header>

      {/* ── Persistent high-risk banner ── */}
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

      {/* ── Body: queue | center | decision ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        <ScanQueue
          scans={scans}
          loading={qLoading}
          error={qError}
          selectedId={selectedId}
          onSelect={handleSelect}
          onRefresh={refresh}
          acqMode={acqMode}
          onModeChange={setAcqMode}
          cameraEnabled={CAMERA_ENABLED}
        />

        {/* Center */}
        <main ref={mainRef} tabIndex={-1} className="flex-1 min-w-0 overflow-y-auto focus:outline-none" style={{ padding: "18px 20px" }}>
          {acqMode === "upload" ? (
            <ImageScreening />
          ) : sLoading ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-4 text-content-muted" style={{ minHeight: 400 }}>
              <span className="animate-spin" style={{ width: 40, height: 40, border: "3px solid rgba(148,163,184,0.25)", borderTopColor: "#94a3b8", borderRadius: 999 }} />
              <span style={{ fontSize: 13, color: "#8595ad" }} role="status">{LOADING}</span>
            </div>
          ) : sError ? (
            <div className="flex flex-col items-center justify-center gap-3" style={{ minHeight: 400 }}>
              <p style={{ fontSize: 13, color: "#fca5a5" }} role="alert">{SCAN_LOAD_ERROR}</p>
              <button onClick={refreshScan} style={chromeBtn}>{RETRY}</button>
            </div>
          ) : scan ? (
            <div className="animate-rise-in"><VerdictPanel
              scan={scan}
              judgements={judgements}
              annotations={annotations}
              onJudgementsChange={setJudgements}
              onAnnotationsChange={setAnnotations}
            /></div>
          ) : (
            <LiveCamera />
          )}
        </main>

        {/* Right: decision column (camera mode + a scan is open) */}
        {acqMode === "camera" && scan && !sLoading && (
          <aside className="shrink-0 overflow-y-auto border-l border-white/10" style={{ width: 320, background: "rgba(255,255,255,0.015)" }}>
            <DecisionPanel
              scan={scan}
              operatorId={auth.operatorId}
              judgements={judgements}
              annotations={annotations}
              onDecided={handleDecided}
            />
          </aside>
        )}
      </div>

      {/* ── Audit slide-over ── */}
      {showAudit && selectedId && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 60 }}>
          <div onClick={toggleAudit} className="absolute inset-0" style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(2px)" }} />
          <div className="relative h-full flex flex-col animate-slide-in-right"
            style={{ width: 430, background: "#0b0e17", borderLeft: "1px solid rgba(255,255,255,0.1)", boxShadow: "-24px 0 64px rgba(0,0,0,0.55)" }}>
            <div className="flex items-center justify-between shrink-0 border-b border-white/10" style={{ padding: "16px 18px" }}>
              <div>
                <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em", color: "#7c8aa3", fontWeight: 600 }}>Chain of custody</div>
                <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: "-0.02em" }}>{AUDIT_TITLE}</div>
                <div className="font-mono" style={{ fontSize: 11, color: "#5b6679", marginTop: 2 }}>{selectedId}</div>
              </div>
              <button onClick={toggleAudit} aria-label="Yopish" className="grid place-items-center"
                style={{ width: 32, height: 32, borderRadius: 9, cursor: "pointer", color: "#aebbcf", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.12)" }}>
                <X size={16} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto" style={{ padding: 18 }}>
              <AuditLog entries={IS_MOCK ? MOCK_AUDIT : auditEntries} chainValid={auditChainValid} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------
function Clock() {
  const [time, setTime] = useState(() => fmtTime());
  useEffect(() => {
    const id = setInterval(() => setTime(fmtTime()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="font-mono tabular-nums" style={{ fontSize: 14, fontWeight: 500, color: "#cbd5e1", letterSpacing: "0.02em", padding: "4px 10px", borderRadius: 8, background: "rgba(0,0,0,0.25)" }}>
      {time}
    </span>
  );
}
function fmtTime() {
  return new Date().toLocaleTimeString("uz-Latn-UZ", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ------------------------------------------------------------------
// Mock audit entries for dev mode
// ------------------------------------------------------------------
const MOCK_AUDIT: AuditEntry[] = [
  {
    event_id: "evt-001", seq: 1, event_type: "acquisition_recorded", operator_id: null,
    payload: { scanner_id: "smiths-lane-1", modality: "dual_energy" },
    created_at: new Date(Date.now() - 5 * 60_000).toISOString(), event_hmac: "a".repeat(64),
  },
  {
    event_id: "evt-002", seq: 2, event_type: "detection_recorded", operator_id: null,
    payload: { n_detections: 2, model: "YOLOv8-xray" },
    created_at: new Date(Date.now() - 3 * 60_000).toISOString(), event_hmac: "b".repeat(64),
  },
  {
    event_id: "evt-003", seq: 3, event_type: "verdict_recorded", operator_id: null,
    payload: { overall_risk: "high", model: "Qwen3-VL" },
    created_at: new Date(Date.now() - 1 * 60_000).toISOString(), event_hmac: "c".repeat(64),
  },
];
