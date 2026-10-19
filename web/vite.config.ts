/// <reference types="vitest/config" />
import fs from "node:fs";
import path from "node:path";
import type { Connect } from "vite";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const API_TARGET = "http://127.0.0.1:8765";
const root = import.meta.dirname;

/** Every web/*.html page: index.html serves the engineer app, <name>.html serves /<name>. */
const pages = Object.fromEntries(
  fs
    .readdirSync(root)
    .filter((file) => file.endsWith(".html"))
    .map((file) => [path.basename(file, ".html"), path.resolve(root, file)]),
);

/** Rewrites /<name> and /<name>/* to /<name>.html for each page other than index; every other
 * unknown path falls back to index.html. */
function pageRoutes(): Plugin {
  const rewrite: Connect.NextHandleFunction = (req, _res, next) => {
    const match = /^\/([^/?.]+)(?:\/[^?.]*)?(?:\?.*)?$/.exec(req.url ?? "");
    const name = match?.[1];
    if (name && name !== "index" && fs.existsSync(path.resolve(root, `${name}.html`))) {
      req.url = `/${name}.html`;
    }
    next();
  };
  return {
    name: "page-routes",
    configureServer: (server) => void server.middlewares.use(rewrite),
    configurePreviewServer: (server) => void server.middlewares.use(rewrite),
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), pageRoutes()],
  resolve: {
    alias: { "@": path.resolve(root, "src") },
  },
  server: {
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq, req) => {
            // A compressed event stream would be buffered until it ends.
            if (req.headers.accept?.includes("text/event-stream")) proxyReq.removeHeader("accept-encoding");
          });
          proxy.on("proxyRes", (proxyRes, _req, res) => {
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              res.setHeader("cache-control", "no-cache");
              res.setHeader("x-accel-buffering", "no");
            }
          });
        },
      },
    },
  },
  build: {
    rolldownOptions: { input: pages },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
