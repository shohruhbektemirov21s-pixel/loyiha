// Tests for the image-screening presentation maps.
//
// Pins two safety-critical invariants:
//   1. risk_band → style map is complete and uses the matching risk-* tokens.
//   2. flag value → style map pairs colour with an icon, and "YO'Q"
//      (not-detected) is NEVER styled green/"clear" — it must stay neutral so
//      the operator never reads it as a clearance.

import { describe, it, expect } from "vitest";
import { isValidElement } from "react";
import { SCREEN_RISK_UI, SCREEN_FLAG_UI } from "./screeningUi";
import { SCREEN_FLAG_NAME, SCREEN_FLAG_VALUE } from "../lib/uz";
import type { RiskBand, ScreenFlag, ScreenFlags } from "../lib/types";

const ALL_BANDS: RiskBand[] = ["clear", "low", "medium", "high"];
const ALL_FLAGS: ScreenFlag[] = ["BOR", "SHUBHALI", "YO'Q"];

describe("SCREEN_RISK_UI (risk_band → style)", () => {
  it("covers every RiskBand value with classes + an icon element", () => {
    for (const b of ALL_BANDS) {
      const ui = SCREEN_RISK_UI[b];
      expect(ui, b).toBeTruthy();
      expect(ui.cls.length).toBeGreaterThan(0);
      expect(isValidElement(ui.icon), `${b} icon`).toBe(true);
    }
  });

  it("each band maps to its matching risk-* colour token", () => {
    expect(SCREEN_RISK_UI.high.cls).toContain("risk-high");
    expect(SCREEN_RISK_UI.medium.cls).toContain("risk-medium");
    expect(SCREEN_RISK_UI.low.cls).toContain("risk-low");
    expect(SCREEN_RISK_UI.clear.cls).toContain("risk-clear");
  });
});

describe("SCREEN_FLAG_UI (flag value → style)", () => {
  it("covers every flag value with classes + an icon element", () => {
    for (const f of ALL_FLAGS) {
      const ui = SCREEN_FLAG_UI[f];
      expect(ui, f).toBeTruthy();
      expect(ui.cls.length).toBeGreaterThan(0);
      expect(isValidElement(ui.icon), `${f} icon`).toBe(true);
    }
  });

  it("BOR (present) is danger-red, SHUBHALI (suspected) is warning-amber", () => {
    expect(SCREEN_FLAG_UI.BOR.cls).toContain("risk-high");
    expect(SCREEN_FLAG_UI.SHUBHALI.cls).toContain("risk-medium");
  });

  it("YO'Q (not detected) is neutral — never styled green/'clear'", () => {
    const cls = SCREEN_FLAG_UI["YO'Q"].cls;
    expect(cls).not.toContain("risk-clear");
    expect(cls).not.toContain("green");
    expect(cls).toContain("surface");
  });
});

describe("flag localization maps", () => {
  it("SCREEN_FLAG_NAME covers every flag key", () => {
    for (const key of ["narcotics", "weapon", "tobacco", "other"] as Array<keyof ScreenFlags>) {
      expect(SCREEN_FLAG_NAME[key]).toBeTruthy();
    }
  });

  it("SCREEN_FLAG_VALUE covers every flag value", () => {
    for (const f of ALL_FLAGS) {
      expect(SCREEN_FLAG_VALUE[f]).toBeTruthy();
    }
  });
});
