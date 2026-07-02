import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
        ws: true,
        // VLM skrining (/v1/screen) iGPU'da bir necha daqiqa olishi mumkin.
        // Node'ning default 5-daqiqalik socket timeout'i so'rovni uzib qo'yardi
        // ("rasm yuklash ishlamayapti"). Backend XRAY_VLM_TIMEOUT_S=600 ga moslab
        // proxy timeout'larini 660s ga ko'taramiz (incoming + target ulanish).
        timeout: 660000,
        proxyTimeout: 660000,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
