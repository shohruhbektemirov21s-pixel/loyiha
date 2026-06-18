// Detection sorting tests (BO'SHLIQ-10).
//
// The console sorts detections two ways:
//   * LiveCamera: pure score-descending (top-3 preview).
//   * VerdictPanel: category-severity descending, then score descending.
// Both comparators are component-local (not exported), so we replicate the EXACT
// production comparator here and pin its behaviour. If the production ordering
// changes, this test must be updated in lock-step — it is the documented sort
// contract for the operator queue (most dangerous, most confident first).

import { describe, it, expect } from "vitest";
import type { ThreatCategory } from "./types";

interface Det { category: ThreatCategory; score: number; id: string; }

// Mirror of VerdictPanel.tsx CATEGORY_SEVERITY.
const CATEGORY_SEVERITY: Record<ThreatCategory, number> = {
  explosive: 100,
  firearm: 90,
  bladed_weapon: 80,
  narcotics: 70,
  contraband_other: 50,
  currency: 40,
  metallic_anomaly: 30,
  organic_anomaly: 20,
  unknown: 10,
};

function verdictSort(dets: Det[]): Det[] {
  return [...dets].sort((a, b) => {
    const sev = CATEGORY_SEVERITY[b.category] - CATEGORY_SEVERITY[a.category];
    if (sev !== 0) return sev;
    return b.score - a.score;
  });
}

function scoreSort(dets: Det[]): Det[] {
  return [...dets].sort((x, y) => y.score - x.score);
}

describe("VerdictPanel detection sort (severity, then score)", () => {
  it("orders by category severity first", () => {
    const dets: Det[] = [
      { id: "cur", category: "currency", score: 0.99 },
      { id: "exp", category: "explosive", score: 0.10 },
      { id: "fir", category: "firearm", score: 0.50 },
    ];
    const order = verdictSort(dets).map((d) => d.id);
    expect(order).toEqual(["exp", "fir", "cur"]);
  });

  it("breaks ties within a category by score descending", () => {
    const dets: Det[] = [
      { id: "low", category: "firearm", score: 0.40 },
      { id: "high", category: "firearm", score: 0.95 },
      { id: "mid", category: "firearm", score: 0.70 },
    ];
    const order = verdictSort(dets).map((d) => d.id);
    expect(order).toEqual(["high", "mid", "low"]);
  });

  it("does not mutate the input array", () => {
    const dets: Det[] = [
      { id: "a", category: "currency", score: 0.1 },
      { id: "b", category: "explosive", score: 0.1 },
    ];
    const before = dets.map((d) => d.id);
    verdictSort(dets);
    expect(dets.map((d) => d.id)).toEqual(before);
  });
});

describe("LiveCamera preview sort (score descending)", () => {
  it("sorts highest-confidence detections first", () => {
    const dets: Det[] = [
      { id: "a", category: "unknown", score: 0.2 },
      { id: "b", category: "unknown", score: 0.9 },
      { id: "c", category: "unknown", score: 0.5 },
    ];
    const order = scoreSort(dets).map((d) => d.id);
    expect(order).toEqual(["b", "c", "a"]);
  });
});
