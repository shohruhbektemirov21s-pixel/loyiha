import { useWsStatus } from "../hooks/useWebSocket";
import { hexA } from "../lib/theme";
import { CONN_OPEN, CONN_CONNECTING, CONN_CLOSED } from "../lib/uz";

// Persistent connection-state indicator — a single coloured pill.
// Green "Ulangan" when the realtime feed is alive; amber while connecting;
// red "Aloqa uzildi" when it is not. Silence must never look like safety.
export function ConnectionStatus() {
  const status = useWsStatus();

  const { color, label, live } =
    status === "open"
      ? { color: "#22c55e", label: CONN_OPEN, live: "polite" as const }
      : status === "connecting"
      ? { color: "#f59e0b", label: CONN_CONNECTING, live: "polite" as const }
      : { color: "#ef4444", label: CONN_CLOSED, live: "assertive" as const };

  return (
    <span
      className={`inline-flex items-center ${status === "closed" ? "animate-pulse" : ""}`}
      role="status"
      aria-live={live}
      style={{
        gap: 7, fontSize: 13, fontWeight: 600, padding: "5px 12px", borderRadius: 999,
        color, background: hexA(color, 0.12), border: `1px solid ${hexA(color, 0.35)}`,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 7, height: 7, borderRadius: 999, background: color,
          boxShadow: status === "open" ? `0 0 8px ${color}` : undefined,
        }}
      />
      {label}
    </span>
  );
}
