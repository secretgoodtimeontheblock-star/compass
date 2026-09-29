import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Сборка кладётся прямо в пакет Python: FastAPI раздаёт её с того же порта, что и API,
// поэтому в проде нет CORS и не нужен второй сервер. В dev /api проксируется на движок.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/compass/static", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
  test: { environment: "node" },
});
