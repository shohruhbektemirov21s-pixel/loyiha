import { useEffect, useRef } from "react";
import { ShieldAlert, Volume2, VolumeX, X } from "lucide-react";
import type { RiskBand } from "../lib/types";
import {
  HIGH_ALERT_TITLE, HIGH_ALERT_BODY, HIGH_ALERT_OPEN, HIGH_ALERT_DISMISS,
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

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="relative z-20 flex items-center gap-3 px-4 py-2.5 bg-risk-high-bg border-b-2 border-risk-high-border text-risk-high-text halo-high animate-slide-in"
    >
      <span className="grid place-items-center w-9 h-9 rounded-lg bg-red-950/60 border border-red-700/60 shadow-glow-high shrink-0" aria-hidden="true">
        <ShieldAlert size={22} className="text-red-400 animate-pulse" />
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-bold">{HIGH_ALERT_TITLE}</p>
        <p className="text-sm text-red-200/90">{HIGH_ALERT_BODY}</p>
      </div>

      <button
        onClick={() => onOpen(alert.scanId)}
        className="press shrink-0 px-3 py-1.5 rounded-lg text-sm font-semibold bg-gradient-to-b from-red-500 to-red-600 hover:from-red-400 hover:to-red-500 text-white shadow-elev-2 transition-all"
      >
        {HIGH_ALERT_OPEN}
      </button>

      <button
        onClick={onToggleSound}
        aria-pressed={soundEnabled}
        aria-label={soundEnabled ? SOUND_ON : SOUND_OFF}
        title={soundEnabled ? SOUND_ON : SOUND_OFF}
        className="shrink-0 p-1.5 rounded text-red-200 hover:bg-red-900/50 transition-colors"
      >
        {soundEnabled ? <Volume2 size={16} aria-hidden="true" /> : <VolumeX size={16} aria-hidden="true" />}
      </button>

      <button
        onClick={onDismiss}
        aria-label={HIGH_ALERT_DISMISS}
        title={HIGH_ALERT_DISMISS}
        className="shrink-0 p-1.5 rounded text-red-200 hover:bg-red-900/50 transition-colors"
      >
        <X size={16} aria-hidden="true" />
      </button>
    </div>
  );
}
