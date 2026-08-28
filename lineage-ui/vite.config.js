import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two build targets from one source:
//
//   npm run build                 -> served by FastAPI at the domain root
//   BASE=/repo/ npm run build     -> GitHub Pages, served from a subpath
//
// GitHub Pages project sites live under /<repo>/, so asset URLs and the
// bundled demo graph have to be prefixed. `import.meta.env.BASE_URL` carries
// that prefix into the app.
const base = process.env.BASE || "/";
const outDir = process.env.OUT_DIR || "../src/pbilineage/web/dist";
// A static build has no API behind it, so it must not waste a request
// probing for one. Set explicitly rather than inferred from the base path,
// which is "/" for both a root-served static site and the FastAPI build.
const isStatic = process.env.STATIC === "1";

export default defineConfig({
  base,
  define: { __STATIC_BUILD__: JSON.stringify(isStatic) },
  plugins: [react()],
  build: {
    outDir,
    emptyOutDir: true,
  },
  server: {
    port: 5174,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    include: ["src/**/*.test.js"],
  },
});
