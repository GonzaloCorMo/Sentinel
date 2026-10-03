# Frontend (Vue 3 + Vite + Tailwind v4)

## Estructura

- `src/views/` — una vista por ruta (`router/index.ts`). El dashboard de operador cuelga de `layouts/AppShellLayout.vue` (cabecera, navegación, panel de sugerencias de la IA y asistente).
- `src/components/dashboard/AmbulanceMap.vue` — mapa principal (MapLibre nativo). `components/ui/StatusChip.vue` — estado de unidad.
- `src/stores/` — Pinia: `simulation.ts` (estado por SSE `/api/sim/stream`, acciones de control), `region.ts`, `builder.ts` (herramienta activa del mapa), `entities.ts`.
- `src/lib/` — `mapEngine.ts` (estilo y utilidades de mapa), `unitStatus.ts` (fase de misión → etiqueta + tono), `sanitize.ts` (DOMPurify), `energyDisplay.ts`, `vehicleId.ts`.
- `src/i18n/locales/{es,en,gl}.json` — todos los textos visibles.
- `src/assets/main.css` (tokens y componentes) + `theme-scales.css` (escalas generadas; no se edita a mano).

## Sistema visual

- **Tema**: `data-theme="dark|light"` en `<html>` (oscuro por defecto). Se gestiona con `composables/useTheme.ts`; el script de `index.html` lo aplica antes de pintar para evitar parpadeo.
- **Color**: las familias de Tailwind están redirigidas en `@theme` a escalas que cambian con el tema:
  - `slate`/`gray`/`zinc` son neutros que se invierten entre temas;
  - `emerald`/`purple`/`cyan`… quedan en monocromo;
  - `green` = ok, `amber` = aviso, `red`/`rose` = crítico.
  - Usa color vivo **solo** para estados, alertas y KPIs críticos. Todo lo demás, en neutros.
- **Forma**: radios de 0 a 4 px (`rounded`, `rounded-sm`), separadores de 1 px (`border-slate-800`), sin degradados ni glows. Las cifras, en `font-mono` (JetBrains Mono, numerales tabulares).
- **Botones**: el primario es `bg-slate-100 text-slate-950 hover:bg-slate-300` (se invierte solo); el secundario, `border border-slate-700 hover:bg-slate-800`.
- **Títulos de página**: `<h1 class="text-lg font-semibold tracking-tight text-slate-100">` más una línea `text-sm text-slate-500` que explica para qué sirve la página.
- Los estilos de capas propias van en `@layer base`/`@layer components`; los overrides de terceros (MapLibre, vue-sonner), sin capa, para que ganen a sus hojas.

## Textos (i18n)

- Ningún texto visible en la plantilla: siempre `t('seccion.clave')`, en `es`, `en` y `gl` a la vez (`bash scripts/verify.sh frontend` detecta claves desalineadas).
- Valores del backend (`missionPhase`, `status`, tipo y gravedad de evento, `linkState`…) **nunca** se muestran en crudo. Tradúcelos:
  - estados de unidad: `unitStatus()` → `status.*`;
  - eventos: `events.type.*` y `events.severity.*`;
  - emergencias: `map.emergency_status.*` y `emergency_type.*`.
- Plurales: `t(key, { n }, n)`; con una cadena del tipo `"{n} unidad | {n} unidades"`.

## Mapas (`lib/mapEngine.ts`)

- MapLibre GL nativo con teselas vectoriales de OpenFreeMap (sin API key) y **un único estilo para ambos temas**, escrito a mano en `lib/mapStyle.ts` sobre el esquema OpenMapTiles. Imita a Google Maps de día: vías blancas con borde gris, autopistas en amarillo, anchos por zoom, edificios desde z15 y POIs solo desde z16. Al tocarlo, valida con `validateStyleMin` de `@maplibre/maplibre-gl-style-spec`: un error de estilo deja el mapa en blanco sin más aviso que la consola.
- Las capas propias (rutas, cortes, áreas) se insertan con `map.addLayer(spec, FIRST_LABEL_LAYER)` para quedar bajo los nombres de calles, como en Google.
- Crea mapas con `await createMap(el, { center: [lat, lon], zoom })`: espera a que el estilo cargue y aplica `ResizeObserver`. El contenedor necesita alto y ancho explícitos (`h-full w-full`, no `absolute inset-0`: MapLibre fuerza `position: relative`).
- Marcadores: `maplibregl.Marker({ element })`, actualizados en su sitio (no se recrean en cada tick). Líneas y áreas: fuentes GeoJSON con `setGeoJson()`. Tooltips: `createHoverTooltip()`.
- El mapa base es claro también en tema oscuro, así que marcadores y popups usan colores fijos (`.sentinel-map`, `MAP_COLORS`), no tokens del tema.
- **Marcadores = nodos tácticos** (`lib/tacticalMarkers.ts`): SVG con contorno de 1,5 px y relleno oscuro al 90 %.
  - Forma por categoría: cuadrado = infraestructura, rectángulo = vehículo, rombo = emergencia.
  - Color solo en el borde: cian = operativa, ámbar = aviso, rojo = emergencia, gris = infraestructura o apagada.
  - Etiquetas `[AMB-001]` en monoespaciada, pegadas a la forma; el nombre completo va en el tooltip.
  - Sin emoji, imágenes, pines en gota, sombras ni escalados; la única animación es el ping de las emergencias críticas sin asignar.
  - La agrupación es en píxeles y depende del zoom (`renderNodes`). No vuelvas a una rejilla fija.
- Si `vite.config.ts` cambia, mantén `optimizeDeps.exclude: ["maplibre-gl"]` y `worker.format: "es"`: el worker de MapLibre 6 se registra con `setWorkerUrl` a partir de `?worker&url`.

## Gráficos

`components/dashboard/TelemetryLineChart.vue` es un SVG propio: sin ECharts, que pesaba unos 600 kB. Una magnitud por gráfico (nunca doble eje) y umbrales en ámbar discontinuo.

## Comprobación

`npm run typecheck` · `npm run build` · revisión visual en `/map`, `/overview`, `/fleet`, `/comms`, `/reports`, `/config`, `/vehicle` (usuario vehicle) y `/message-alert`, en ambos temas.
