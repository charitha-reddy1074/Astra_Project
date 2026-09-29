import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      // Dev: proxy API calls to the FastAPI backend.
      "/api": { target: "http://127.0.0.1:8787", changeOrigin: true },
    },
  },
});
