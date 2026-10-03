/**
 * Tema de la aplicación: `dark` (por defecto) o `light`.
 *
 * El atributo `data-theme` de <html> lo fija un script inline de
 * `index.html` antes del primer pintado (sin parpadeo); aquí solo se
 * expone el estado reactivo y el cambio persistido en localStorage.
 */
import { readonly, ref } from "vue";

export type Theme = "dark" | "light";

const STORAGE_KEY = "sentinel.theme";

function readInitial(): Theme {
  const attr = document.documentElement.dataset.theme;
  return attr === "light" ? "light" : "dark";
}

const theme = ref<Theme>(readInitial());

function apply(next: Theme) {
  theme.value = next;
  document.documentElement.dataset.theme = next;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", next === "dark" ? "#0b0c0e" : "#ffffff");
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* modo privado: el tema dura lo que la sesión */
  }
}

export function useTheme() {
  return {
    theme: readonly(theme),
    setTheme: apply,
    toggleTheme: () => apply(theme.value === "dark" ? "light" : "dark"),
  };
}

/** Lee un token CSS resuelto para el tema activo (para Leaflet / ECharts). */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
