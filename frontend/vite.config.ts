import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      // Same-origin in dev, so the browser never deals with CORS or a second host.
      // Follows FTP_API_PORT so a second environment (start-ai.sh) proxies
      // to its own API rather than the main one.
      "/api": { target: `http://127.0.0.1:${process.env.FTP_API_PORT || 8099}`,
                changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      output: {
        // ECharts is most of the bundle and changes far less often than the
        // app, so splitting it keeps app deploys off the user's download path.
        manualChunks: { echarts: ["echarts"], react: ["react", "react-dom"] },
      },
    },
  },
});
