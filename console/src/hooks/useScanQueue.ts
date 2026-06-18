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

  const fetch = useCallback(async () => {
    if (IS_MOCK) { setScans(MOCK_SCANS); setLoading(false); return; }
    try {
      const res = await listScans({ lane_id: laneId ?? undefined, limit: 50 });
      setScans(res.items);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Xato");
    } finally {
      setLoading(false);
    }
  }, [laneId]);

  fetchRef.current = fetch;

  useEffect(() => {
    void fetch();
    const id = setInterval(() => void fetchRef.current?.(), POLL_MS);
    return () => clearInterval(id);
  }, [fetch]);

  // WS events nudge a refresh so the queue updates in real-time.
  useWebSocket((msg: WsMessage) => {
    if (
      msg.type === "scan_flagged" ||
      msg.type === "scan_analyzed" ||
      msg.type === "scan_decided"
    ) {
      void fetchRef.current?.();
    }
  }, laneId);

  const refresh = useCallback(() => void fetch(), [fetch]);

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
