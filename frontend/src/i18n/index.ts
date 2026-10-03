/**
 * Configuración i18n (vue-i18n) — 3 locales: es (default), gl, en.
 *
 * - Locale persistido en localStorage (`sentinel.locale`).
 * - Detecta `navigator.language` la primera vez si no hay preferencia guardada.
 * - Fallback siempre a español.
 *
 * Uso en componentes:
 *   <script setup>
 *   import { useI18n } from "vue-i18n";
 *   const { t } = useI18n();
 *   </script>
 *   <template>{{ t("header.login") }}</template>
 */
import { createI18n } from "vue-i18n";
import en from "./locales/en.json";
import es from "./locales/es.json";
import gl from "./locales/gl.json";

export type Locale = "es" | "gl" | "en";

export const SUPPORTED_LOCALES: { code: Locale; name: string; short: string }[] = [
  { code: "es", name: "Español", short: "ES" },
  { code: "gl", name: "Galego", short: "GL" },
  { code: "en", name: "English", short: "EN" },
];

const STORAGE_KEY = "sentinel.locale";

function detectInitialLocale(): Locale {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "es" || stored === "gl" || stored === "en") return stored;
  const nav = (navigator.language || "").toLowerCase();
  if (nav.startsWith("gl")) return "gl";
  if (nav.startsWith("en")) return "en";
  return "es";
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: detectInitialLocale(),
  fallbackLocale: "es",
  messages: { es, gl, en },
});

export function setLocale(locale: Locale) {
  i18n.global.locale.value = locale;
  localStorage.setItem(STORAGE_KEY, locale);
  document.documentElement.lang = locale;
}

// Sincroniza atributo lang inicial.
document.documentElement.lang = i18n.global.locale.value;
