/** Centro inicial del mapa (fallback estático antes de cargar la región activa).
 *
 * El selector de mapas (`useRegionStore`) trae la lista real desde
 * `/api/regions` y reposiciona el mapa al cambiar. Estas constantes solo se
 * usan en el primer pintado — Aruba (región por defecto del backend).
 */
export const MAP_DEFAULT_CENTER: [number, number] = [12.5398, -70.0344];
export const MAP_DEFAULT_ZOOM = 14;

/** Spawn fallback antes de que el store de regiones cargue. */
export const DEFAULT_SPAWN_LAT = 12.5407;
export const DEFAULT_SPAWN_LON = -70.0347;
