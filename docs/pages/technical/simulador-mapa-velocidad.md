# Simulador: velocidad y ruta en el mapa

Notas de comportamiento del dashboard operativo (`/dashboard`) respecto al multiplicador de velocidad de simulación y a la polilínea de ruta.

## Multiplicador de velocidad

- El estado del motor se **refresca por polling** (unos pocos segundos).
- El campo numérico del multiplicador **no** se sobrescribe en cada poll mientras el usuario lo está editando: solo se sincroniza con `state.stats.simulationSpeed` cuando el campo no está en modo “sucio” (dirty), p. ej. tras **Aplicar** con éxito o al abandonar el foco sin edición pendiente según la lógica del componente.
- Los límites y el paso del input están alineados con `POST /api/sim/control/speed` (rango típico 0.5–8).

## Ruta en el mapa

### Recorte del tramo ya recorrido

- La geometría completa puede obtenerse del motor Python o, en fallback, de OSRM.
- En el cliente se aplica **`trimRouteFromPosition`** (`src/lib/geo/trim-route.ts`): se proyecta la posición actual de la ambulancia sobre la polilínea y se dibuja solo el **tramo restante** hacia el destino, de modo que el recorrido completado deja de mostrarse al avanzar la unidad.

### Fallback OSRM y emergencia correcta

- Si el motor no devuelve una polilínea útil, el servicio puede trazar una ruta aproximada vía OSRM.
- `GET /api/sim/route/[ambulanceId]` admite el query opcional **`?emergencyId=...`**: el destino del fallback se resuelve respecto a esa emergencia en lugar de asumir “la primera del listado”.
- El dashboard envía `emergencyId` cuando hay una emergencia seleccionada en el panel.

### Refetch de la geometría

- La petición de ruta al API se dispara al cambiar ambulancia o emergencia seleccionada, o cuando cambia el marcador temporal del estado (`updatedAt`). El **recorte visual** se recalcula en cada actualización de posición de la ambulancia seleccionada, sin necesidad de volver a pedir la geometría en cada tick.

## Referencias de código

| Concepto | Ubicación |
|----------|-----------|
| Input de velocidad y carga de ruta | `proyecto_hpe/src/components/dashboard/dashboard-client.tsx` |
| Recorte de polilínea | `proyecto_hpe/src/lib/geo/trim-route.ts` |
| Fallback de ruta y `emergencyId` | `proyecto_hpe/src/lib/services/simulation/simulation-service.ts` |
| API | `proyecto_hpe/app/api/sim/route/[ambulanceId]/route.ts` |
