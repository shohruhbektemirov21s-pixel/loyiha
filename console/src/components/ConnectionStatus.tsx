import { Wifi, WifiOff, Loader2 } from "lucide-react";
import { useWsStatus } from "../hooks/useWebSocket";
import { CONN_OPEN, CONN_CONNECTING, CONN_CLOSED } from "../lib/uz";

// Persistent connection-state indicator.
// Green "Ulangan" when the realtime feed is alive; red "Aloqa uzildi" when it
// is not. Silence must never look like safety — the operator always knows.
export function ConnectionStatus() {
  const status = useWsStatus();

  if (status === "open") {
    return (
      <span
        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-sm font-medium bg-green-900/40 text-green-300 border border-green-800/60"
        role="status"
        aria-live="polite"
      >
        <Wifi size={14} aria-hidden="true" />
        {CONN_OPEN}
      </span>
    );
  }

  if (status === "connecting") {
    return (
      <span
        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-sm font-medium bg-amber-900/40 text-amber-300 border border-amber-800/60"
        role="status"
        aria-live="polite"
      >
        <Loader2 size={14} className="animate-spin" aria-hidden="true" />
        {CONN_CONNECTING}
      </span>
    );
  }

  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-sm font-semibold bg-red-900/60 text-red-200 border border-red-700 animate-pulse"
      role="status"
      aria-live="assertive"
    >
      <WifiOff size={14} aria-hidden="true" />
      {CONN_CLOSED}
    </span>
  );
}
