// Localization + risk-band mapping tests (BO'SHLIQ-10).
//
// Verifies the risk_band -> Uzbek label maps are complete and that NO
// operator-facing localized string contains a forbidden "pass freely" clearance
// phrase (the console must never render text that tells the operator a scan is
// safe to pass). Also pins the CLEAR disclaimer's advisory wording.

import { describe, it, expect } from "vitest";
import * as uz from "./uz";
import type { RiskBand } from "./types";

const ALL_BANDS: RiskBand[] = ["clear", "low", "medium", "high"];

describe("risk band -> label maps", () => {
  it("RISK_BAND covers every RiskBand value", () => {
    for (const b of ALL_BANDS) {
      expect(uz.RISK_BAND[b]).toBeTruthy();
    }
  });

  it("RISK_BAND_SHORT covers every RiskBand value", () => {
    for (const b of ALL_BANDS) {
      expect(uz.RISK_BAND_SHORT[b]).toBeTruthy();
    }
  });

  it("high band maps to the 'yuqori' (high) wording", () => {
    expect(uz.RISK_BAND.high.toLowerCase()).toContain("yuqori");
    expect(uz.RISK_BAND_SHORT.high.toLowerCase()).toContain("yuqori");
  });

  it("clear band wording does NOT assert the cargo is safe", () => {
    // "Shubhali buyum aniqlanmadi" = "no suspicious item detected" — a finding
    // statement, not a clearance. It must not say "xavfsiz"/"o'tkazing"/etc.
    const clear = uz.RISK_BAND.clear.toLowerCase();
    expect(clear).not.toContain("xavfsiz");
    expect(clear).not.toContain("o'tkaz");
  });
});

// ---------------------------------------------------------------------------
// Forbidden clearance phrases must never appear in ANY localized UI string.
// ---------------------------------------------------------------------------
const FORBIDDEN = [
  "xavfsiz",          // "safe"
  "o'tkazing",        // "let it through" (imperative)
  "o‘tkazing",
  "bemalol o'tadi",   // "passes freely"
  "ruxsat bering",    // "give permission"
  "xavf yo'q",        // "no risk"
  "xatar yo'q",       // "no danger"
  "muammo yo'q",      // "no problem"
];

describe("no forbidden clearance phrase in localized UI strings", () => {
  // Collect every exported string / Record<string,string> value from uz.ts.
  const allStrings: string[] = [];
  for (const value of Object.values(uz)) {
    if (typeof value === "string") allStrings.push(value);
    else if (value && typeof value === "object") {
      for (const v of Object.values(value as Record<string, unknown>)) {
        if (typeof v === "string") allStrings.push(v);
      }
    }
  }

  it("collected a non-trivial number of UI strings", () => {
    expect(allStrings.length).toBeGreaterThan(30);
  });

  it.each(FORBIDDEN)("no UI string contains %s", (phrase) => {
    const hits = allStrings.filter((s) => s.toLowerCase().includes(phrase.toLowerCase()));
    expect(hits, `forbidden phrase ${phrase} in: ${JSON.stringify(hits)}`).toEqual([]);
  });

  it("CLEAR_DISCLAIMER frames clear as advisory, operator-decides", () => {
    const d = uz.CLEAR_DISCLAIMER.toLowerCase();
    // Must mention operator authority and must not declare safety.
    expect(d).toContain("operator");
    expect(d).not.toContain("xavfsiz");
  });
});

// ---------------------------------------------------------------------------
// The continuous camera stream emits risk_band "unavailable" (fail-safe, when
// the detector/VLM seam is unwired). It must be mapped to a label that is NOT
// a clearance. Verdict-facing RISK_BAND deliberately stays clear/low/medium/high;
// the camera-specific CAMERA_RISK_SHORT carries the extra "unavailable" state.
// (Was a BUG: LiveCamera crashed on an "unavailable" frame — now fixed.)
// ---------------------------------------------------------------------------
describe("camera 'unavailable' band coverage", () => {
  it("CAMERA_RISK_SHORT maps 'unavailable' to a non-empty label", () => {
    expect(uz.CAMERA_RISK_SHORT.unavailable).toBeTruthy();
  });

  it("CAMERA_RISK_SHORT covers every normal RiskBand too", () => {
    for (const b of ["clear", "low", "medium", "high"] as const) {
      expect(uz.CAMERA_RISK_SHORT[b]).toBeTruthy();
    }
  });

  it("the 'unavailable' label does not read as a clearance", () => {
    const label = uz.CAMERA_RISK_SHORT.unavailable.toLowerCase();
    // must not imply "clear / safe / no finding"
    expect(label).not.toContain("aniqlanmadi");
    expect(label).not.toContain("xavfsiz");
  });

  it("verdict-facing RISK_BAND stays limited to the four decision bands", () => {
    expect((uz.RISK_BAND as Record<string, string>).unavailable).toBeUndefined();
  });
});
