/** Centro inicial del mapa (fallback estático antes de cargar la región activa).
 *
 * El selector de mapas (`useRegionStore`) trae la lista real desde
 * `/api/regions` y reposiciona el mapa al cambiar. Estas constantes solo se
 * usan en el primer pintado — Madrid (Las Rozas) como default.
 */
export const MAP_DEFAULT_CENTER: [number, number] = [40.4933, -3.8742];
export const MAP_DEFAULT_ZOOM = 14;

/** Spawn fallback antes de que el store de regiones cargue. */
export const DEFAULT_SPAWN_LAT = 40.4942;
export const DEFAULT_SPAWN_LON = -3.8745;
