import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND =
  process.env.BACKEND_BASE_URL ||
  "https://ca-mpwflow-dev-chat-api.icyground-4e2c6fde.eastus2.azurecontainerapps.io";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: BACKEND,
        changeOrigin: true,
        secure: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
