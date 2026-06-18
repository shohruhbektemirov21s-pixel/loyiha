// Typed API client — thin fetch wrapper, no third-party HTTP library.
// All requests carry the JWT bearer token from sessionStorage.
// No egress: all calls go to /v1/* which Vite proxies to localhost:8000.

import type {
  ScanListResponse,
  ScanRecord,
  Detection,
  DetectionResult,
  OperatorVerdict,
  ImageFrame,
  ThreatCategory,
  RiskBand,
  OperatorFeedback,
  FeedbackReceipt,
  TokenResponse,
} from "./types";

const BASE = "/v1";

// ---------------------------------------------------------------------------
// Backend response shapes (app/api/v1/scans.py) — flat, DB-flavored.
// These differ from the contracts/v1 mirror in types.ts, so we adapt below.
// ---------------------------------------------------------------------------
interface RawDetection {
  detection_id: string;
  frame_id:     string;
  category:     ThreatCategory;
  native_label: string;
  score:        number;
  box_x:        number;
  box_y:        number;
  box_width:    number;
  box_height:   number;
  calibrated:   boolean;
}

interface RawVerdict {
  verdict_id:    string;
  overall_risk:  RiskBand;
  summary_uz:    string;
  model_name:    string;
  model_version: string;
  per_detection: Array<{
    detection_id: string;
    category:     ThreatCategory;
    confidence:   number;
    rationale_uz: string;
  }>;
  generated_at:  string;
}

interface RawScanBase {
  scan_id:      string;
  scanner_id:   string;
  lane_id:      string | null;
  subject:      ScanRecord["subject"];
  modality:     ScanRecord["modality"];
  state:        ScanRecord["state"];
  overall_risk: RiskBand | null;
  acquired_at:  string;
  analyzed_at:  string | null;
  verdicted_at: string | null;
  decided_at:   string | null;
}

interface RawFrame {
  frame_id:   string;
  width_px:   number;
  height_px:  number;
  media_type: string;
}

interface RawScanDetail extends RawScanBase {
  frames:     RawFrame[];
  detections: RawDetection[];
  verdict:    RawVerdict | null;
}

interface RawScanList {
  items:     RawScanBase[];
  total:     number;
  page:      number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// Adapters: backend flat shape → frontend contract shape
// ---------------------------------------------------------------------------
function adaptDetection(d: RawDetection): Detection {
  return {
    detection_id: d.detection_id,
    frame_id:     d.frame_id,
    box:          { x: d.box_x, y: d.box_y, width: d.box_width, height: d.box_height },
    native_label: d.native_label,
    category:     d.category,
    score:        d.score,
    crop:         null,
    attributes:   {},
  };
}

function adaptDetectionResult(s: RawScanDetail): DetectionResult | null {
  // Prefer the backend's real frame descriptors; otherwise synthesize from
  // detection frame_ids. With neither, there is nothing to show.
  let frames: ImageFrame[];
  if (s.frames?.length) {
    frames = s.frames.map((f) => ({
      frame_id:         f.frame_id,
      width_px:         f.width_px,
      height_px:        f.height_px,
      image:            { uri: "", media_type: f.media_type, sha256: "", size_bytes: 0 },
      view_label:       null,
      pixel_spacing_mm: null,
    }));
  } else if (s.detections.length) {
    const frameIds = [...new Set(s.detections.map((d) => d.frame_id))];
    frames = frameIds.map((fid) => ({
      frame_id:         fid,
      width_px:         1024,
      height_px:        768,
      image:            { uri: "", media_type: "image/png", sha256: "", size_bytes: 0 },
      view_label:       null,
      pixel_spacing_mm: null,
    }));
  } else {
    return null;
  }
  return {
    schema_version: "1.0",
    scan_id:        s.scan_id,
    status:         "completed",
    emitted_at:     s.analyzed_at ?? s.acquired_at,
    model:          { name: "detector", version: "", weights_sha256: null, runtime: null },
    frames,
    detections:     s.detections.map(adaptDetection),
    error:          null,
  };
}

function adaptVerdict(s: RawScanDetail): OperatorVerdict | null {
  const v = s.verdict;
  if (!v) return null;
  return {
    schema_version: "1.0",
    verdict_id:     v.verdict_id,
    scan_id:        s.scan_id,
    locale:         "uz-Latn",
    overall_risk:   v.overall_risk,
    summary_uz:     v.summary_uz,
    per_detection:  (v.per_detection ?? []).map((pd) => ({
      detection_id: pd.detection_id,
      category:     pd.category,
      rationale_uz: pd.rationale_uz,
      confidence:   pd.confidence,
    })),
    model:          { name: v.model_name, version: v.model_version, weights_sha256: null, runtime: null },
    generated_at:   v.generated_at,
    decision_support_only: true,
  };
}

function adaptScanBase(s: RawScanBase): ScanRecord {
  return {
    scan_id:      s.scan_id,
    scanner_id:   s.scanner_id,
    lane_id:      s.lane_id,
    modality:     s.modality,
    subject:      s.subject,
    state:        s.state,
    overall_risk: s.overall_risk,
    acquired_at:  s.acquired_at,
    analyzed_at:  s.analyzed_at,
    verdicted_at: s.verdicted_at,
    decided_at:   s.decided_at,
    detection:    null,
    verdict:      null,
  };
}

function adaptScanDetail(s: RawScanDetail): ScanRecord {
  return {
    ...adaptScanBase(s),
    detection: adaptDetectionResult(s),
    verdict:   adaptVerdict(s),
  };
}

// ---------------------------------------------------------------------------
// Auth token management
// ---------------------------------------------------------------------------
const TOKEN_KEY = "xray_token";

export function saveToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function loadToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

// Broadcast that the session is no longer valid. The App listens for this and
// returns to the login screen — WITHOUT reloading the page (a reload on every
// 401 creates an infinite loop when the stored token is stale).
export const AUTH_EXPIRED_EVENT = "xray:auth-expired";
export function notifyAuthExpired(): void {
  clearToken();
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------
class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = loadToken();
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  };

  const res = await fetch(`${BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    notifyAuthExpired();
    throw new ApiError(401, "Sessiya tugadi. Qayta kiring.");
  }

  if (!res.ok) {
    let body: unknown;
    try { body = await res.json(); } catch { /* ignore */ }
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as Record<string, unknown>).detail)
        : res.statusText;
    throw new ApiError(res.status, detail, body);
  }

  // 204 No Content — return empty object
  if (res.status === 204) return {} as T;

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
export async function login(
  username: string,
  password: string,
): Promise<TokenResponse> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    throw new ApiError(res.status, "Foydalanuvchi nomi yoki parol noto'g'ri.");
  }
  return res.json() as Promise<TokenResponse>;
}

// ---------------------------------------------------------------------------
// Scans
// ---------------------------------------------------------------------------
export async function listScans(params?: {
  state?: string;
  lane_id?: string;
  limit?: number;
  offset?: number;
}): Promise<ScanListResponse> {
  const qs = new URLSearchParams();
  if (params?.state)   qs.set("state",     params.state);
  if (params?.lane_id) qs.set("lane_id",   params.lane_id);
  if (params?.limit)   qs.set("page_size", String(params.limit));
  const q = qs.toString() ? `?${qs}` : "";
  const raw = await request<RawScanList>(`/scans${q}`);
  return {
    items:  raw.items.map(adaptScanBase),
    total:  raw.total,
    offset: (raw.page - 1) * raw.page_size,
    limit:  raw.page_size,
  };
}

export async function getScan(scanId: string): Promise<ScanRecord> {
  const raw = await request<RawScanDetail>(`/scans/${scanId}`);
  return adaptScanDetail(raw);
}

export async function markReviewing(scanId: string): Promise<ScanRecord> {
  return request<ScanRecord>(`/scans/${scanId}/review`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// Camera capture
// ---------------------------------------------------------------------------
export interface CaptureResult {
  scan_id:      string;
  state:        string;
  overall_risk: string | null;
  summary_uz:   string | null;
  frame_id:     string;
  width_px:     number;
  height_px:    number;
}

export async function captureCamera(): Promise<CaptureResult> {
  return request<CaptureResult>("/camera/capture", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Live camera stream (continuous MJPEG preview + analysis)
// ---------------------------------------------------------------------------
export interface CameraStreamStatus {
  running:           boolean;
  device:            string | null;
  cadence_s:        number | null;
  last_analysis_ts: string | null;
  frames_analyzed:  number;
}

// Authenticated <img> src for the live MJPEG preview.
export function cameraLiveUrl(): string {
  const token = loadToken();
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${BASE}/camera/live.mjpg${qs}`;
}

export async function startCameraStream(opts?: {
  device?: string;
  cadence_s?: number;
}): Promise<CameraStreamStatus> {
  return request<CameraStreamStatus>("/camera/stream/start", {
    method: "POST",
    body: JSON.stringify({
      device:    opts?.device ?? null,
      cadence_s: opts?.cadence_s ?? null,
    }),
  });
}

export async function stopCameraStream(): Promise<CameraStreamStatus> {
  return request<CameraStreamStatus>("/camera/stream/stop", { method: "POST" });
}

export async function getCameraStreamStatus(): Promise<CameraStreamStatus> {
  return request<CameraStreamStatus>("/camera/stream/status");
}

// ---------------------------------------------------------------------------
// Operator decision (confirm / reject) → archive
// ---------------------------------------------------------------------------
export interface DecisionResult {
  scan_id:    string;
  state:      string;
  outcome:    string;
  decided_at: string;
}

export async function decideScan(
  scanId: string,
  decision: "confirmed" | "rejected",
  note?: string,
): Promise<DecisionResult> {
  return request<DecisionResult>(`/scans/${scanId}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision, note: note ?? null }),
  });
}

// ---------------------------------------------------------------------------
// Image proxy
// The API serves image bytes at GET /v1/scans/{scan_id}/frames/{frame_id}
// (or equivalent). We construct an authenticated URL for <img> src.
// ---------------------------------------------------------------------------
export function frameImageUrl(scanId: string, frameId: string): string {
  const token = loadToken();
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return `/v1/scans/${scanId}/frames/${encodeURIComponent(frameId)}${qs}`;
}

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------
export async function submitFeedback(
  feedback: OperatorFeedback,
): Promise<FeedbackReceipt> {
  return request<FeedbackReceipt>("/feedback", {
    method: "POST",
    body: JSON.stringify(feedback),
  });
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------
export interface AuditEntry {
  event_id:    string;
  seq:         number;
  event_type:  string;
  operator_id: string | null;
  payload:     Record<string, unknown>;
  created_at:  string;
  event_hmac:  string;
}

export async function getScanAudit(scanId: string): Promise<AuditEntry[]> {
  return request<AuditEntry[]>(`/scans/${scanId}/audit`);
}

export { ApiError };
