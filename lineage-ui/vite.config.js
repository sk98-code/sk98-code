import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the Python package so FastAPI serves the bundle and the
// wheel ships it (see [tool.setuptools.package-data] in pyproject.toml).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/pbilineage/web/dist",
    emptyOutDir: true,
  },
  server: {
    port: 5174,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
