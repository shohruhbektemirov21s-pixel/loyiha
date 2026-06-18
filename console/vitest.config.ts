/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Vitest config for the operator console. jsdom gives us a DOM + localStorage +
// a WebSocket-shaped global we can stub, so the WS client and React components
// can be unit-tested without a browser. Run with: npx vitest run
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
