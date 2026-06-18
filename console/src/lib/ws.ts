// WebSocket client — real-time scan notifications from /v1/ws.
// Handles reconnection with exponential backoff and ping/pong keepalive.

import type { WsMessage } from "./types";
import { loadToken } from "./api";

export type WsHandler = (msg: WsMessage) => void;

const PING_MS        = 20_000;
const BASE_DELAY_MS  = 2_000;
const MAX_DELAY_MS   = 30_000;
const MAX_RECONNECTS = 20;

export class ScanWebSocket {
  private ws:          WebSocket | null = null;
  private pingTimer:   ReturnType<typeof setInterval> | null = null;
  private reconnects:  number  = 0;
  private stopped:     boolean = false;
  private handlers:    Set<WsHandler> = new Set();

  constructor(private readonly laneId: string | null = null) {}

  subscribe(handler: WsHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  connect(): void {
    this.stopped = false;
    this._open();
  }

  disconnect(): void {
    this.stopped = true;
    this._clearPing();
    this.ws?.close(1000, "client disconnect");
    this.ws = null;
  }

  private _open(): void {
    const token = loadToken();
    const qs = new URLSearchParams();
    if (token)        qs.set("token",   token);
    if (this.laneId)  qs.set("lane_id", this.laneId);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url   = `${proto}://${location.host}/v1/ws?${qs}`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.reconnects = 0;
      this._startPing();
    };

    this.ws.onmessage = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(ev.data as string) as WsMessage;
        if (msg.type === "ping") {
          this.ws?.send(JSON.stringify({ type: "pong" }));
          return;
        }
        this.handlers.forEach((h) => h(msg));
      } catch { /* malformed — discard */ }
    };

    this.ws.onclose = () => {
      this._clearPing();
      if (!this.stopped) this._scheduleReconnect();
    };

    this.ws.onerror = () => { this.ws?.close(); };
  }

  private _startPing(): void {
    this._clearPing();
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN)
        this.ws.send(JSON.stringify({ type: "ping" }));
    }, PING_MS);
  }

  private _clearPing(): void {
    if (this.pingTimer !== null) { clearInterval(this.pingTimer); this.pingTimer = null; }
  }

  private _scheduleReconnect(): void {
    if (this.reconnects >= MAX_RECONNECTS) return;
    const delay = Math.min(BASE_DELAY_MS * 2 ** this.reconnects, MAX_DELAY_MS);
    this.reconnects++;
    setTimeout(() => { if (!this.stopped) this._open(); }, delay);
  }
}
