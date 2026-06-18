// TypeScript mirrors of the Python contracts/v1 schema.
// Keep in sync with contracts/v1/*.py — same field names, same semantics.

// ---------------------------------------------------------------------------
// Common primitives
// ---------------------------------------------------------------------------
export type ScanId       = string;   // UUID
export type DetectionId  = string;   // UUID
export type VerdictId    = string;   // UUID
export type FeedbackId   = string;   // UUID
export type FrameId      = string;
export type OperatorId   = string;
export type Sha256Hex    = string;   // 64 lowercase hex chars
export type UnitInterval = number;   // [0, 1]

export type ScanSubject   = "vehicle" | "cargo" | "baggage" | "parcel" | "other";
export type ImageModality = "single_energy" | "dual_energy" | "multi_view";

export type ThreatCategory =
  | "narcotics"
  | "firearm"
  | "bladed_weapon"
  | "explosive"
  | "currency"
  | "organic_anomaly"
  | "metallic_anomaly"
  | "contraband_other"
  | "unknown";

export type RiskBand = "clear" | "low" | "medium" | "high";

// Continuous camera analysis may report "unavailable" when the detector/VLM
// seam is unwired — a fail-safe state, NOT a clearance. Never collapse to "clear".
export type CameraRiskBand = RiskBand | "unavailable";

export interface StorageRef {
  uri:        string;
  media_type: string;
  sha256:     Sha256Hex;
  size_bytes: number;
}

export interface PixelBox {
  x:      number;
  y:      number;
  width:  number;
  height: number;
}

export interface ImageFrame {
  frame_id:        FrameId;
  width_px:        number;
  height_px:       number;
  image:           StorageRef;
  view_label:      string | null;
  pixel_spacing_mm: number | null;
}

export interface ModelProvenance {
  name:            string;
  version:         string;
  weights_sha256:  Sha256Hex | null;
  runtime:         string | null;
}

// ---------------------------------------------------------------------------
// Detection (Hop 2)
// ---------------------------------------------------------------------------
export type DetectionStatus =
  | "completed"
  | "completed_no_findings"
  | "failed";

export interface Detection {
  detection_id:  DetectionId;
  frame_id:      FrameId;
  box:           PixelBox;
  native_label:  string;
  category:      ThreatCategory;
  score:         UnitInterval;
  crop:          StorageRef | null;
  attributes:    Record<string, string>;
}

export interface DetectionResult {
  schema_version: "1.0";
  scan_id:        ScanId;
  status:         DetectionStatus;
  emitted_at:     string;
  model:          ModelProvenance;
  frames:         ImageFrame[];
  detections:     Detection[];
  error:          string | null;
}

// ---------------------------------------------------------------------------
// Verdict (Hop 3)
// ---------------------------------------------------------------------------
export type Locale = "uz-Latn" | "uz-Cyrl" | "ru";

export interface DetectionVerdict {
  detection_id:  DetectionId;
  category:      ThreatCategory;
  rationale_uz:  string;
  confidence:    UnitInterval;
}

export interface OperatorVerdict {
  schema_version:        "1.0";
  verdict_id:            VerdictId;
  scan_id:               ScanId;
  locale:                Locale;
  overall_risk:          RiskBand;
  summary_uz:            string;
  per_detection:         DetectionVerdict[];
  model:                 ModelProvenance;
  generated_at:          string;
  decision_support_only: true;
}

// ---------------------------------------------------------------------------
// Feedback (Hop 4)
// ---------------------------------------------------------------------------
export type DetectionJudgement =
  | "confirmed"
  | "rejected"
  | "reclassified"
  | "unreviewed";

export type OperatorOutcome =
  | "cleared"
  | "inspected"
  | "seized"
  | "escalated";

export interface DetectionReview {
  detection_id:       DetectionId;
  judgement:          DetectionJudgement;
  corrected_category: ThreatCategory | null;
  note_uz:            string | null;
}

export interface OperatorAnnotation {
  frame_id:  FrameId;
  box:       PixelBox;
  category:  ThreatCategory;
  note_uz:   string | null;
}

export interface OperatorFeedback {
  schema_version: "1.0";
  feedback_id:    FeedbackId;
  scan_id:        ScanId;
  verdict_id:     VerdictId | null;
  operator_id:    OperatorId;
  detection:      DetectionResult;
  outcome:        OperatorOutcome;
  reviews:        DetectionReview[];
  missed:         OperatorAnnotation[];
  decided_at:     string;
  emitted_at:     string;
  notes_uz:       string | null;
}

export interface FeedbackReceipt {
  schema_version:       "1.0";
  feedback_id:          FeedbackId;
  scan_id:              ScanId;
  labels_queued:        number;
  hard_negatives_queued: number;
  accepted_at:          string;
  dataset_target:       string | null;
}

// ---------------------------------------------------------------------------
// Scan lifecycle (from app/db/models.py ScanState)
// ---------------------------------------------------------------------------
export type ScanState =
  | "pending"
  | "analyzing"
  | "analyzed"
  | "verdicted"
  | "reviewing"
  | "decided"
  | "error";

// Enriched scan record from GET /v1/scans/{scan_id}
export interface ScanRecord {
  scan_id:       ScanId;
  scanner_id:    string;
  lane_id:       string | null;
  modality:      ImageModality;
  subject:       ScanSubject;
  state:         ScanState;
  overall_risk:  RiskBand | null;
  acquired_at:   string;
  analyzed_at:   string | null;
  verdicted_at:  string | null;
  decided_at:    string | null;
  detection:     DetectionResult | null;
  verdict:       OperatorVerdict | null;
}

// Paginated scan list from GET /v1/scans
export interface ScanListResponse {
  items:   ScanRecord[];
  total:   number;
  offset:  number;
  limit:   number;
}

// ---------------------------------------------------------------------------
// WebSocket notification messages (canonical wire format — dotted "type",
// matching the backend exactly: "scan.flagged", "scan.analyzed", ...).
// Do NOT use underscores here; the backend emits dots.
// ---------------------------------------------------------------------------
export type WsMessageType =
  | "scan.flagged"
  | "scan.analyzed"
  | "scan.decided"
  | "camera.analysis"
  | "ping"
  | "pong";

export interface WsScanFlagged {
  type:          "scan.flagged";
  scan_id:       ScanId;
  lane_id:       string | null;
  risk_band:     RiskBand;
  n_detections:  number;
  ts:            string;
}

export interface WsScanAnalyzed {
  type:    "scan.analyzed";
  scan_id: ScanId;
  lane_id: string | null;
  ts:      string;
}

export interface WsScanDecided {
  type:    "scan.decided";
  scan_id: ScanId;
  lane_id: string | null;
  outcome: OperatorOutcome;
  ts:      string;
}

// Live camera analysis — one per analyzed frame from the camera agent.
export interface CameraDetectionLite {
  category: ThreatCategory;
  score:    UnitInterval;
  box_x:    number;
  box_y:    number;
  box_w:    number;
  box_h:    number;
}

export interface WsCameraAnalysis {
  type:          "camera.analysis";
  device:        string;
  ts:            string;
  risk_band:     CameraRiskBand;
  n_detections:  number;
  summary_uz:    string;
  detections:    CameraDetectionLite[];
}

export interface WsPing { type: "ping"; }
export interface WsPong { type: "pong"; }

export type WsMessage =
  | WsScanFlagged
  | WsScanAnalyzed
  | WsScanDecided
  | WsCameraAnalysis
  | WsPing
  | WsPong;

// ---------------------------------------------------------------------------
// Auth (from app/auth/models.py)
// ---------------------------------------------------------------------------
export type OperatorRole = "operator" | "supervisor" | "admin";

export interface TokenResponse {
  access_token: string;
  token_type:   "bearer";
  expires_in:   number;
  operator_id:  string;
  username:     string;
  role:         OperatorRole;
  lane_ids:     string[];
}

export interface AuthState {
  token:       string;
  operatorId:  string;
  username:    string;
  role:        OperatorRole;
  laneIds:     string[];
}
