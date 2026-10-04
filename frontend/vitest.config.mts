import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Tests must never load local credentials or contact real map services.
  envDir: false,
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    env: {
      NEXT_PUBLIC_AMAP_JS_KEY: "test-js-key",
      NEXT_PUBLIC_AMAP_SERVICE_HOST: "http://127.0.0.1:8000/_AMapService",
      AMAP_WEB_KEY: "",
      AMAP_JS_KEY: "",
      AMAP_JS_SECURITY_CODE: "",
    },
  },
});
