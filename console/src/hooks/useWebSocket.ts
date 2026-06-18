import {
  createContext, createElement, useContext, useEffect, useRef, useState,
  type ReactNode,
} from "react";
import { ScanWebSocket, type WsHandler, type WsStatus } from "../lib/ws";
import { IS_MOCK } from "../lib/mock";

// ---------------------------------------------------------------------------
// Single shared WebSocket connection.
//
// Previously App and useScanQueue each opened their own ScanWebSocket, so the
// browser held two parallel connections. We now expose ONE connection through
// a context provider; every consumer subscribes to the same socket.
// ---------------------------------------------------------------------------

interface WsContextValue {
  subscribe: (handler: WsHandler) => () => void;
  status:    WsStatus;
}

const WsContext = createContext<WsContextValue | null>(null);

export function WebSocketProvider({
  laneId,
  children,
}: {
  laneId: string | null;
  children: ReactNode;
}) {
  // In mock/demo mode there is no API server — report "closed" but never connect.
  const [status, setStatus] = useState<WsStatus>("closed");
  const wsRef = useRef<ScanWebSocket | null>(null);

  useEffect(() => {
    if (IS_MOCK) {
      setStatus("closed");
      return;
    }
    const ws = new ScanWebSocket(laneId);
    wsRef.current = ws;
    const unsubStatus = ws.subscribeStatus(setStatus);
    ws.connect();
    return () => {
      unsubStatus();
      ws.disconnect();
      wsRef.current = null;
    };
  }, [laneId]);

  const value: WsContextValue = {
    subscribe: (handler) => {
      const ws = wsRef.current;
      if (!ws) return () => {};
      return ws.subscribe(handler);
    },
    status,
  };

  return createElement(WsContext.Provider, { value }, children);
}

// Subscribe to WS messages. The handler ref is kept fresh so callers can pass
// an inline closure without re-subscribing every render.
export function useWebSocket(handler: WsHandler): void {
  const ctx = useContext(WsContext);
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!ctx) return;
    return ctx.subscribe((msg) => handlerRef.current(msg));
    // ctx.subscribe is stable enough; re-running on ctx identity is harmless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx]);
}

// Read the live connection status (for the connection indicator).
export function useWsStatus(): WsStatus {
  const ctx = useContext(WsContext);
  return ctx?.status ?? "closed";
}
