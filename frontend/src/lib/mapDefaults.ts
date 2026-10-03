/** Centro inicial del mapa (fallback estático antes de cargar la región activa).
 *
 * El selector de mapas (`useRegionStore`) trae la lista real desde
 * `/api/regions` y reposiciona el mapa al cambiar. Estas constantes solo se
 * usan en el primer pintado — Santiago de Compostela (región por defecto).
 */
export const MAP_DEFAULT_CENTER: [number, number] = [42.871, -8.564];
export const MAP_DEFAULT_ZOOM = 14;

/** Spawn fallback antes de que el store de regiones cargue. */
export const DEFAULT_SPAWN_LAT = 42.8702;
export const DEFAULT_SPAWN_LON = -8.5648;
