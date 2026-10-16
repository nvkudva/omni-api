import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

const GATEWAY = "http://127.0.0.1:10000";

export default defineConfig({
  plugins: [preact()],
  base: "/static/app/",
  build: { outDir: "../src/omni/static/app", emptyOutDir: true },
  server: {
    port: 5180,
    proxy: { "/v1": GATEWAY, "/admin": GATEWAY, "/health": GATEWAY },
  },
});
