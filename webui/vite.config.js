import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the Python package so FastAPI serves the bundle and
// the wheel ships it (see [tool.setuptools.package-data]).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/bimigrate/web/static/dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8400" },
  },
});
