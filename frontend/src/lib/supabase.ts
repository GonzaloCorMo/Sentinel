/**
 * Singleton del cliente Supabase Auth + DB para el frontend.
 *
 * Usa la clave `anon` con RLS activa — nunca la service-role key aquí.
 * El cliente apunta al proxy `/sb` de Vite (mismo origen que el frontend)
 * para funcionar sobre móvil con un solo puerto/túnel.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null = null;

/** True si las variables `VITE_SUPABASE_URL` y `ANON_KEY` están configuradas. */
export function isSupabaseConfigured(): boolean {
  const url = import.meta.env.VITE_SUPABASE_URL;
  const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;
  return Boolean(url && anonKey);
}

/**
 * Resuelve la URL base de Supabase al mismo origen que sirve el frontend,
 * usando el proxy `/sb` de Vite (ver vite.config.ts). Ventajas:
 *   - Móvil solo necesita el puerto 5173 abierto (no 54321).
 *   - Un único túnel HTTPS (ngrok) cubre todo → secure-origin → geo/voz OK.
 *   - Evita mixed-content y problemas de CORS entre orígenes.
 *
 * Fallback al valor de `.env` si no hay window (SSR, tests).
 */
function resolveSupabaseUrl(): string {
  if (typeof window !== "undefined") {
    return window.location.origin + "/sb";
  }
  return (import.meta.env.VITE_SUPABASE_URL as string) || "";
}

/** Devuelve el cliente singleton (PKCE flow) o `null` si faltan env vars. */
export function getSupabase(): SupabaseClient | null {
  if (!isSupabaseConfigured()) return null;
  if (!client) {
    const url = resolveSupabaseUrl();
    const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;
    client = createClient(url, anonKey, {
      auth: {
        flowType: "pkce",
        autoRefreshToken: true,
        persistSession: true,
        detectSessionInUrl: false,
      },
    });
  }
  return client;
}
