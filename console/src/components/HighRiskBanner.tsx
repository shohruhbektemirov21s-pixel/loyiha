import { useEffect, useRef } from "react";
import { AlertTriangle, X } from "lucide-react";
import type { RiskBand } from "../lib/types";
import {
  HIGH_ALERT_TITLE, HIGH_ALERT_OPEN, HIGH_ALERT_DISMISS,
  SOUND_ON, SOUND_OFF,
} from "../lib/uz";

export interface HighRiskAlert {
  scanId:   string;
  riskBand: RiskBand;
  ts:       string;
}

interface Props {
  alert:        HighRiskAlert;
  soundEnabled: boolean;
  onToggleSound:() => void;
  onOpen:       (scanId: string) => void;
  onDismiss:    () => void;
}

// Plays a short attention tone via Web Audio (no asset files, no autoplay of
// media elements). Returns a cleanup that stops the oscillator.
function playBeep(): void {
  try {
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.4);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.42);
    osc.onended = () => void ctx.close();
  } catch {
    /* audio unavailable — visual banner is the primary signal */
  }
}

// Persistent high-risk banner — stays until the operator acts (open or dismiss).
// A distinct icon (ShieldAlert) and explicit text back up the red colour so it
// is not colour-only.
export function HighRiskBanner({
  alert, soundEnabled, onToggleSound, onOpen, onDismiss,
}: Props) {
  // Re-beep whenever a new alert (new scan id) arrives, if sound is enabled.
  const lastBeepedRef = useRef<string | null>(null);
  useEffect(() => {
    if (soundEnabled && lastBeepedRef.current !== alert.scanId) {
      lastBeepedRef.current = alert.scanId;
      playBeep();
    }
  }, [alert.scanId, soundEnabled]);

  // Time shown in the secondary line — derived from the alert timestamp as HH:MM.
  const time = (() => {
    const d = new Date(alert.ts);
    return Number.isNaN(d.getTime())
      ? alert.ts
      : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  })();
  const shortId = alert.scanId.slice(0, 8) + "…";

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="animate-drop-down"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "11px 18px",
        background: "rgba(239,68,68,0.13)",
        borderBottom: "1px solid rgba(239,68,68,0.4)",
        flex: "none",
      }}
    >
      <span
        className="animate-pulse"
        aria-hidden="true"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 34,
          height: 34,
          borderRadius: 9,
          background: "rgba(239,68,68,0.2)",
          color: "#fca5a5",
          flex: "none",
        }}
      >
        <AlertTriangle size={20} />
      </span>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: "#fecaca" }}>
          {HIGH_ALERT_TITLE}
        </div>
        <div style={{ fontSize: 12.5, color: "#fca5a5" }}>
          <span className="font-mono">{shortId}</span>
          {" · "}
          {time}
        </div>
      </div>

      <button
        onClick={onToggleSound}
        aria-pressed={soundEnabled}
        aria-label={soundEnabled ? SOUND_ON : SOUND_OFF}
        title={soundEnabled ? SOUND_ON : SOUND_OFF}
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: "#fca5a5",
          padding: "6px 11px",
          borderRadius: 8,
          background: "rgba(0,0,0,0.2)",
          border: "1px solid rgba(239,68,68,0.3)",
        }}
      >
        {soundEnabled ? SOUND_ON : SOUND_OFF}
      </button>

      <button
        onClick={() => onOpen(alert.scanId)}
        style={{
          fontSize: 12.5,
          fontWeight: 700,
          color: "#fff",
          padding: "7px 16px",
          borderRadius: 8,
          background: "#dc2626",
          border: "none",
        }}
      >
        {HIGH_ALERT_OPEN}
      </button>

      <button
        onClick={onDismiss}
        aria-label={HIGH_ALERT_DISMISS}
        title={HIGH_ALERT_DISMISS}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 30,
          height: 30,
          borderRadius: 8,
          color: "#fca5a5",
          background: "transparent",
          border: "1px solid rgba(239,68,68,0.25)",
        }}
      >
        <X size={15} aria-hidden="true" />
      </button>
    </div>
  );
}
