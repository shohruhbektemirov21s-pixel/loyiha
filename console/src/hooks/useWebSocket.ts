import { useEffect, useRef } from "react";
import { ScanWebSocket, type WsHandler } from "../lib/ws";
import { IS_MOCK } from "../lib/mock";

export function useWebSocket(
  handler: WsHandler,
  laneId: string | null = null,
): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (IS_MOCK) return;   // no API server in dev/demo mode
    const ws = new ScanWebSocket(laneId);
    const unsub = ws.subscribe((msg) => handlerRef.current(msg));
    ws.connect();
    return () => {
      unsub();
      ws.disconnect();
    };
  }, [laneId]);
}
