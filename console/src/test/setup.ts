// Vitest global setup: jest-dom matchers + a deterministic localStorage and a
// controllable WebSocket stub the ws.ts client can drive.
import "@testing-library/jest-dom/vitest";

// jsdom provides localStorage, but clear it between tests for isolation.
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.sessionStorage.clear();
});
