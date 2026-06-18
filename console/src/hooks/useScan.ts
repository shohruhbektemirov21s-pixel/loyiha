import { useState, useEffect, useCallback } from "react";
import { getScan, ApiError } from "../lib/api";
import { MOCK_SCANS, IS_MOCK } from "../lib/mock";
import type { ScanRecord } from "../lib/types";

export function useScan(scanId: string | null) {
  const [scan, setScan]     = useState<ScanRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]   = useState<string | null>(null);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    if (IS_MOCK) {
      const found = MOCK_SCANS.find((s) => s.scan_id === id) ?? null;
      setScan(found);
      setLoading(false);
      return;
    }
    try {
      const s = await getScan(id);
      setScan(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Xato");
      setScan(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (scanId) void load(scanId);
    else { setScan(null); setError(null); }
  }, [scanId, load]);

  const refresh = useCallback(() => {
    if (scanId) void load(scanId);
  }, [scanId, load]);

  return { scan, setScan, loading, error, refresh };
}
