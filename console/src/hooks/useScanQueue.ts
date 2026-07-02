import { useState, useEffect, useCallback, useRef } from "react";
import { listScans, ApiError } from "../lib/api";
import { MOCK_SCANS, IS_MOCK } from "../lib/mock";
import type { ScanRecord, WsMessage } from "../lib/types";
import { useWebSocket } from "./useWebSocket";

const POLL_MS = 15_000;

export function useScanQueue(laneId: string | null = null) {
  const [scans, setScans]   = useState<ScanRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);

  const fetchRef = useRef<() => Promise<void>>();

  // Pure data load — does NOT touch `loading`. The spinner is owned by the
  // callers (initial mount + manual refresh) so background polls and WS nudges
  // never flicker the ↻ button.
  const fetch = useCallback(async () => {
    if (IS_MOCK) { setScans(MOCK_SCANS); return; }
    try {
      const res = await listScans({ lane_id: laneId ?? undefined, limit: 50 });
      setScans(res.items);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Xato");
    }
  }, [laneId]);

  fetchRef.current = fetch;

  useEffect(() => {
    setLoading(true);
    void fetch().finally(() => setLoading(false));
    const id = setInterval(() => void fetchRef.current?.(), POLL_MS);
    return () => clearInterval(id);
  }, [fetch]);

  // WS events nudge a refresh so the queue updates in real-time.
  useWebSocket((msg: WsMessage) => {
    if (
      msg.type === "scan.flagged" ||
      msg.type === "scan.analyzed" ||
      msg.type === "scan.decided"
    ) {
      void fetchRef.current?.();
    }
  });

  // Manual refresh: spin the ↻ button so the operator gets visible feedback even
  // when the result set is unchanged. The local /v1/scans call returns in ~10ms
  // — too fast to perceive — so we hold the spinner for a minimum 450ms window.
  const refresh = useCallback(() => {
    setLoading(true);
    const minSpin = new Promise((r) => setTimeout(r, 450));
    void Promise.all([fetch(), minSpin]).finally(() => setLoading(false));
  }, [fetch]);

  // Update a single scan record in-place (used when a decision is submitted).
  const upsert = useCallback((updated: ScanRecord) => {
    setScans((prev) => {
      const idx = prev.findIndex((s) => s.scan_id === updated.scan_id);
      if (idx === -1) return [updated, ...prev];
      const next = [...prev];
      next[idx] = updated;
      return next;
    });
  }, []);

  return { scans, loading, error, refresh, upsert };
}
