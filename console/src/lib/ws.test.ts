// WebSocket message parsing tests (BO'SHLIQ-10).
//
// Drives the real ScanWebSocket with a fake global WebSocket so we can feed it
// canonical server frames (scan.flagged, camera.analysis) and assert they are
// parsed and dispatched to subscribers, that ping is auto-ponged (and NOT
// dispatched), and that malformed JSON is discarded silently. Deterministic —
// no real socket, no timers fired.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { ScanWebSocket } from "./ws";
import type { WsMessage, WsScanFlagged, WsCameraAnalysis } from "./types";

// ---------------------------------------------------------------------------
// Fake WebSocket: captures the handlers ws.ts assigns and lets the test emit.
// ---------------------------------------------------------------------------
class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = FakeWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(data: string) { this.sent.push(data); }
  close() { this.onclose?.(); }
  // test helper
  emit(obj: unknown) { this.onmessage?.({ data: JSON.stringify(obj) }); }
  emitRaw(data: string) { this.onmessage?.({ data }); }
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  // @ts-expect-error — install the fake on the global used by ws.ts
  globalThis.WebSocket = FakeWebSocket;
  // loadToken() reads sessionStorage key "xray_token".
  window.sessionStorage.setItem("xray_token", "test-token");
});

function connect(): { sock: ScanWebSocket; fake: FakeWebSocket } {
  const sock = new ScanWebSocket("lane-1");
  sock.connect();
  const fake = FakeWebSocket.instances[0];
  fake.onopen?.();
  return { sock, fake };
}

describe("ScanWebSocket message parsing", () => {
  it("parses and dispatches a scan.flagged message", () => {
    const { sock, fake } = connect();
    const received: WsMessage[] = [];
    sock.subscribe((m) => received.push(m));

    const flagged: WsScanFlagged = {
      type: "scan.flagged",
      scan_id: "11111111-1111-1111-1111-111111111111",
      lane_id: "lane-1",
      risk_band: "high",
      n_detections: 2,
      ts: "2026-06-18T10:00:00Z",
    };
    fake.emit(flagged);

    expect(received).toHaveLength(1);
    const msg = received[0] as WsScanFlagged;
    expect(msg.type).toBe("scan.flagged");
    expect(msg.risk_band).toBe("high");
    expect(msg.n_detections).toBe(2);
  });

  it("parses a canonical camera.analysis message", () => {
    const { sock, fake } = connect();
    const received: WsMessage[] = [];
    sock.subscribe((m) => received.push(m));

    const analysis: WsCameraAnalysis = {
      type: "camera.analysis",
      device: "0",
      ts: "2026-06-18T10:00:01Z",
      risk_band: "medium",
      n_detections: 1,
      summary_uz: "Bitta shubhali hudud aniqlandi.",
      detections: [
        { category: "firearm", score: 0.8, box_x: 1, box_y: 2, box_w: 3, box_h: 4 },
      ],
    };
    fake.emit(analysis);

    expect(received).toHaveLength(1);
    const msg = received[0] as WsCameraAnalysis;
    expect(msg.type).toBe("camera.analysis");
    expect(msg.summary_uz).toContain("shubhali");
    expect(msg.detections).toHaveLength(1);
  });

  it("auto-ponds a ping and does NOT dispatch it to subscribers", () => {
    const { sock, fake } = connect();
    const received: WsMessage[] = [];
    sock.subscribe((m) => received.push(m));

    fake.emit({ type: "ping" });

    expect(received).toHaveLength(0); // ping handled internally
    expect(fake.sent).toContain(JSON.stringify({ type: "pong" }));
  });

  it("discards malformed JSON without throwing or dispatching", () => {
    const { sock, fake } = connect();
    const received: WsMessage[] = [];
    sock.subscribe((m) => received.push(m));

    expect(() => fake.emitRaw("{not valid json")).not.toThrow();
    expect(received).toHaveLength(0);
  });

  it("delivers to every subscriber and stops after unsubscribe", () => {
    const { sock, fake } = connect();
    const a: WsMessage[] = [];
    const b: WsMessage[] = [];
    const unsubA = sock.subscribe((m) => a.push(m));
    sock.subscribe((m) => b.push(m));

    fake.emit({ type: "scan.analyzed", scan_id: "x", lane_id: "lane-1", ts: "t" });
    expect(a).toHaveLength(1);
    expect(b).toHaveLength(1);

    unsubA();
    fake.emit({ type: "scan.analyzed", scan_id: "y", lane_id: "lane-1", ts: "t" });
    expect(a).toHaveLength(1); // no longer receiving
    expect(b).toHaveLength(2);
  });
});
