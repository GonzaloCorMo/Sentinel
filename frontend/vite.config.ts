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
  build: {
    rollupOptions: {
      output: {
        // Dependencias pesadas en chunks propios: se cachean entre despliegues.
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (id.includes("leaflet")) return "vendor-leaflet";
          if (id.includes("@supabase")) return "vendor-supabase";
        },
      },
    },
  },
  server: {
    port: 5173,
    // En Docker Desktop (Windows/macOS) los bind mounts no emiten eventos de
    // inotify: sin polling el HMR no ve los cambios de ./src.
    watch: process.env.VITE_USE_POLLING ? { usePolling: true, interval: 300 } : undefined,
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
