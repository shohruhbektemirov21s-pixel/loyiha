// Mock data for development / demo when the API server is offline.
// Import nothing from the API layer — this is standalone.

import type { ScanRecord, DetectionResult, OperatorVerdict } from "./types";

const MINUS = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

const detection: DetectionResult = {
  schema_version: "1.0",
  scan_id:        "00000000-0001-0000-0000-000000000001",
  status:         "completed",
  emitted_at:     MINUS(2),
  model:          { name: "YOLOv8-xray", version: "2.4.1", weights_sha256: null, runtime: "onnxruntime" },
  frames: [
    {
      frame_id:        "frame-01",
      width_px:        1024,
      height_px:       768,
      image:           { uri: "/mock/scan1.png", media_type: "image/png", sha256: "aa".repeat(32), size_bytes: 81920 },
      view_label:      "high_energy",
      pixel_spacing_mm: 0.8,
    },
  ],
  detections: [
    {
      detection_id: "det-001",
      frame_id:     "frame-01",
      box:          { x: 310, y: 200, width: 120, height: 90 },
      native_label: "handgun",
      category:     "firearm",
      score:        0.91,
      crop:         null,
      attributes:   { mean_density: "high", material: "metallic" },
    },
    {
      detection_id: "det-002",
      frame_id:     "frame-01",
      box:          { x: 500, y: 320, width: 80, height: 60 },
      native_label: "organic_blob",
      category:     "organic_anomaly",
      score:        0.43,
      crop:         null,
      attributes:   {},
    },
  ],
  error: null,
};

const verdict: OperatorVerdict = {
  schema_version:        "1.0",
  verdict_id:            "00000000-0002-0000-0000-000000000002",
  scan_id:               "00000000-0001-0000-0000-000000000001",
  locale:                "uz-Latn",
  overall_risk:          "high",
  summary_uz:
    "Chap tomonda kichik metall buyum aniqlandi — 91% ishonch bilan qurol deb tasniflanadi. "
    + "O'ng tomonda organik anomaliya past ishonch darajasida (43%) aniqlandi.",
  per_detection: [
    {
      detection_id: "det-001",
      category:     "firearm",
      rationale_uz:
        "Buyumning shakli va zichligi qo'l qurollariga xos. Detektor 91% ishonch bilan tasniflamoqda.",
      confidence: 0.91,
    },
    {
      detection_id: "det-002",
      category:     "organic_anomaly",
      rationale_uz:
        "Organik material aniqlandi, ammo past ishonch darajasida. Ehtiyotkorlik bilan tekshirish tavsiya etiladi.",
      confidence: 0.43,
    },
  ],
  model:                 { name: "Qwen3-VL", version: "7B-q4", weights_sha256: null, runtime: "llama.cpp" },
  generated_at:          MINUS(1),
  decision_support_only: true,
};

export const MOCK_SCANS: ScanRecord[] = [
  {
    scan_id:      "00000000-0001-0000-0000-000000000001",
    scanner_id:   "smiths-lane-1",
    lane_id:      "1-yo'lak",
    modality:     "dual_energy",
    subject:      "baggage",
    state:        "verdicted",
    overall_risk: "high",
    acquired_at:  MINUS(5),
    analyzed_at:  MINUS(3),
    verdicted_at: MINUS(1),
    decided_at:   null,
    detection,
    verdict,
  },
  {
    scan_id:      "00000000-0001-0000-0000-000000000002",
    scanner_id:   "smiths-lane-1",
    lane_id:      "1-yo'lak",
    modality:     "dual_energy",
    subject:      "baggage",
    state:        "analyzed",
    overall_risk: "medium",
    acquired_at:  MINUS(10),
    analyzed_at:  MINUS(8),
    verdicted_at: null,
    decided_at:   null,
    detection: {
      ...detection,
      scan_id:    "00000000-0001-0000-0000-000000000002",
      detections: [
        {
          detection_id: "det-003",
          frame_id:     "frame-01",
          box:          { x: 200, y: 150, width: 100, height: 70 },
          native_label: "knife",
          category:     "bladed_weapon",
          score:        0.67,
          crop:         null,
          attributes:   {},
        },
      ],
    },
    verdict: null,
  },
  {
    scan_id:      "00000000-0001-0000-0000-000000000003",
    scanner_id:   "smiths-lane-1",
    lane_id:      "1-yo'lak",
    modality:     "dual_energy",
    subject:      "baggage",
    state:        "decided",
    overall_risk: "clear",
    acquired_at:  MINUS(20),
    analyzed_at:  MINUS(18),
    verdicted_at: MINUS(17),
    decided_at:   MINUS(15),
    detection: {
      ...detection,
      scan_id:    "00000000-0001-0000-0000-000000000003",
      status:     "completed_no_findings",
      detections: [],
    },
    verdict: {
      ...verdict,
      scan_id:      "00000000-0001-0000-0000-000000000003",
      overall_risk: "clear",
      summary_uz:   "Shubhali buyum aniqlanmadi. Qaror operatorga tegishli.",
      per_detection: [],
    },
  },
];

export const IS_MOCK = import.meta.env.VITE_MOCK === "true";
