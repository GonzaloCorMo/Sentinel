/**
 * Migra claves de localStorage con el prefijo antiguo a las actuales.
 * Se importa lo primero en main.ts (antes de i18n, que lee el locale al cargar)
 * para que nadie pierda idioma, sesión de chat o servicio de vehículo activo.
 */
const RENAMED: Record<string, string> = {
  "hpe.locale": "sentinel.locale",
  "hpe-sentinel.vehicle": "sentinel.vehicle",
  "hpe-sentinel-chat-session": "sentinel.chat-session",
};

try {
  for (const [from, to] of Object.entries(RENAMED)) {
    const value = localStorage.getItem(from);
    if (value === null) continue;
    if (localStorage.getItem(to) === null) localStorage.setItem(to, value);
    localStorage.removeItem(from);
  }
} catch {
  /* almacenamiento no disponible: nada que migrar */
}
