# Manual de uso

## Alcance

Manual operativo para ejecutar y usar Sentinel, el gemelo digital de la flota de ambulancias.

## 1) Arranque del entorno

1. Inicia servicios con `./up.sh` desde la raíz del repo.
2. Verifica que la app responde en `http://10.10.48.25:5173` (producción) o `http://localhost:5173` (local).
3. Verifica la documentación en `http://10.10.48.25:3001` (o `http://localhost:3001`).
4. Healthcheck rápido del backend: `curl http://10.10.48.25:8080/health`.

## 2) Acceso y autenticación

1. Entra a `/login`.
2. Inicia sesión con email + contraseña, o con Google OAuth.
3. La redirección depende del rol:
   - **`admin`** (operador) → `/map`.
   - **`vehicle`** (piloto) → `/vehicle` (panel del vehículo, ubicación por defecto).
   - **`citizen`** (PWA) → `/message-alert`.

## 3) Idioma (i18n)

Selector de idioma visible en la cabecera y en el login. Tres idiomas: **Español** (default), **Inglés**, **Gallego**. La elección persiste en localStorage.

## 4) Selector de región / mapa

En la cabecera del dashboard hay un `RegionSelector` con 4 opciones: Aruba (default), Madrid, Bogotá, Ciudad de México. Cambiar de región **resetea la simulación** (POIs y flota se borran porque sus coordenadas no son válidas en el nuevo grafo OSRM). El backend conmuta el contenedor OSRM activo automáticamente; los demás quedan idle.

## 5) Mapa de operaciones (`/map`)

Vista principal del gemelo digital con mapa interactivo:

- **Mapa Leaflet** con ambulancias, emergencias, hospitales, gasolineras, estaciones de carga, estaciones meteorológicas, eventos externos y atascos en tiempo real.
- **Barra de herramientas** (lateral izquierda): crear emergencias, ambulancias, hospitales, gasolineras, charging stations, atascos.
- **Paleta de tipos de unidad**: arrastra desde la paleta para spawn de un tipo concreto (combustion / electric / hybrid). El catálogo viene de `fleet_entity_types`.
- **Panel telemetría** (lateral derecha): vitales paciente, mecánica, GPS, environmental, network de la unidad seleccionada.
- **Controles simulación**: play / pause / reset, multiplier 0.1–20×, contador ticks.
- **Pantalla completa**: botón para expandir el mapa.

### Crear entidades

1. Selecciona la herramienta en la barra lateral (ej. "Emergencia").
2. Haz clic en el mapa para colocarla.
3. Rellena el formulario flotante si es necesario (título, nombre del hospital, etc.).

### Severidad de pacientes

| Severidad | Efecto |
|-----------|--------|
| **Stable** (verde) | Vitales normales, situación controlada |
| **Moderate** (ámbar) | Vitales alteradas, atención requerida |
| **Critical** (rojo) | Vitales críticas, el motor IA puede intervenir |

## 6) Telemetría flota (`/fleet`)

Tarjetas de todas las unidades con indicadores de:

- Estado FSM (idle, en_route, on_scene, transporting, at_hospital, refueling/charging).
- Combustible (combustion) o batería (electric) según `powertrain` del tipo.
- Velocidad, RPM, motor health.
- Vitales del paciente (si está transportando) + severidad.
- Filtros por tipo, estado, severidad, energía baja.

Click en una tarjeta para detalles ampliados.

## 7) Vista global (`/island`)

Dashboard agregado de la región activa (datos de `GET /api/island/summary`):

- Agregados meteorológicos (avg temp, max precip, max viento, min visibilidad).
- Eventos externos activos por tipo y severidad (ver [Fuente de eventos](../technical/fuente-de-eventos.md)).
- KPIs de flota (total, emergencias activas, fuel low, ETA media, pulse rate).
- Weather impact: `worstFactor`, `avgFactor`, `affectedMissions`.
- Bucketing por cuadrantes (NW/NE/SW/SE).

## 8) Comunicaciones (`/comms`)

Monitorización de los canales:

- Estado MQTT, P2P mesh, HTTP fallback (toggleable).
- Métricas: mensajes enviados/recibidos, latencia.
- Tabla de actividad por unidad.
- Indicador del canal activo.

## 9) Panel de IA (HITL / autónomo)

Esquina inferior derecha:

- **Propuestas IA**: anomalías + protocolo sugerido + razonamiento LLM.
- **Botones**: aprobar / rechazar.
- **Modo autónomo**: la IA ejecuta sin aprobación. Al activarlo, las pendientes se auto-resuelven.
- **Log**: historial reciente de propuestas resueltas.

### Tipos de anomalías detectadas

| Anomalía | Trigger | Acción propuesta |
|----------|---------|------------------|
| Combustible bajo | fuel < 15% | Redirigir a gasolinera (combustion) |
| Batería baja | battery < 15% | Redirigir a charging station (electric) |
| Vitales críticas | SpO2 < 85% o HR > 140 | Redirigir al hospital más cercano |
| Emergencia desatendida | Sin asignar > 30s | Auto-despachar la unidad más cercana |
| Weather hazard | Eventos meteorológicos cerca | Reroute o pausa según severidad |

## 10) Chatbot ⌘ comando

Chat flotante (esquina inferior izquierda) con dos modos:

- **Pregunta libre** (RAG sobre protocolos): ej. "¿Cuál es el protocolo de IAM?".
- **Comando estructurado** (tool-calling): ej. "muéstrame las ambulancias con combustible bajo", "centra el mapa en AMB-003", "pasa la IA a modo autónomo". Usa un fast-path por regex y el LLM (Gemma) como fallback.

Detalles en [Chatbot IA](chatbot-ia.md).

## 11) Informes post-turno (`/reports`)

Generador LLM de informe operativo:

- Selecciona ventana (default 60 min, máx 24 h).
- Genera con `POST /api/ai/shift-report` → KPIs + highlights + recomendaciones.
- Listado histórico vía `GET /api/ai/shift-reports`.

## 12) Panel del vehículo (`/vehicle`, rol `vehicle`)

- Selector de tipo de unidad en el primer login (combustión, eléctrica, helicóptero, etc.).
- Mapa con ruta OSRM steps turn-by-turn (proxy `/api/osrm/...`).
- Telemetría reducida: posición, ETA, próxima maniobra.
- Botón "He llegado" para avanzar fase de misión manualmente.
- Modo manual (drag pin) o GPS real (`navigator.geolocation`).

## 13) Solución de problemas

- Backend caído → la UI muestra aviso de desconexión y cae a polling `/api/sim/state` cada 2 s.
- Sin persistencia → revisar `VITE_SUPABASE_URL` y `VITE_SUPABASE_ANON_KEY`.
- OSRM no responde → ambulancias usan ruta recta como fallback (badge OSRM en rojo).
- Sin eventos externos ni meteorología → revisar `EVENT_SOURCE` y el estado en `GET /api/events/status`; la simulación local sigue funcionando.
- Recuperación de contraseña Supabase → revisar redirect URLs en Supabase Auth y abrir el enlace en el mismo navegador.

## Referencias

- [Supabase Local Development](https://supabase.com/docs/guides/local-development)
- [OSRM API](http://project-osrm.org/docs/v5.24.0/api/)
- [Runbook resiliencia operativa](../technical/runbook-resiliencia-operativa.md)
