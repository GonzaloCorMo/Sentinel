import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import vueDevTools from "vite-plugin-vue-devtools";

export default defineConfig({
  plugins: [vue(), tailwindcss(), vueDevTools()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.DEV_PROXY_API_TARGET || "http://127.0.0.1:8080",
        changeOrigin: true,
      },
      // /sb/* → Supabase Kong. Expone Supabase por el mismo origen que Vite
      // para que (a) el móvil solo necesite el puerto 5173 abierto, y (b)
      // valga con un único túnel HTTPS (ngrok) para obtener secure-origin
      // sin mixed-content.
      "/sb": {
        target: process.env.DEV_PROXY_SB_TARGET || "http://127.0.0.1:54321",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/sb/, ""),
      },
      // OSRM ya no necesita proxy directo: las llamadas del panel del
      // vehículo (steps turn-by-turn) van a /api/osrm/* y el backend
      // las redirige al OSRM de la región activa.
    },
  },
});
