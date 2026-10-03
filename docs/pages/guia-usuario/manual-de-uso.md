# Manual de uso

## Alcance

Manual operativo para ejecutar y usar Sentinel, el gemelo digital de la flota de ambulancias.

## 1) Arranque del entorno

1. Inicia servicios con `./up.sh` desde la raíz del repo.
2. Verifica que la app responde en `http://localhost:5173`.
3. Verifica la documentación en `http://localhost:3001`.
4. Healthcheck rápido del backend: `curl http://localhost:8080/health`.
5. En el primer arranque, espera a que `ollama-init` descargue los modelos de IA (~2,2 GB); hasta entonces el asistente no responde.

## 2) Acceso y autenticación

1. Entra a `/login`.
2. Inicia sesión con email + contraseña, o con Google OAuth.
3. La redirección depende del rol:
   - **`admin`** (operador) → `/map`.
   - **`vehicle`** (piloto) → `/vehicle` (panel del vehículo, ubicación por defecto).
   - **`citizen`** (PWA) → `/message-alert`.

## 3) Idioma (i18n)

Selector de idioma visible en la cabecera y en el login. Tres idiomas: **Español** (default), **Inglés**, **Gallego**. La elección persiste en localStorage.

## 4) Navegación del dashboard

El menú principal tiene seis secciones:

| Sección | Ruta | Para qué sirve |
|---|---|---|
| **Mapa** | `/map` | Operación en directo: ver y colocar unidades, emergencias y lugares, y controlar la simulación. |
| **Panorama** | `/overview` | Resumen de la región activa: clima, eventos, estado de la flota y reparto por zonas. |
| **Flota** | `/fleet` | Tarjetas con el estado y la telemetría de cada unidad, con filtros. |
| **Comunicaciones** | `/comms` | Enlace de cada unidad, mensajes de la central a las unidades y canales de datos. |
| **Informes** | `/reports` | Informes de turno generados por la IA. |
| **Ajustes** | `/config` | Tipos de unidad y de lugar disponibles en el mapa, y cómo se asignan las emergencias. |

El mapa sigue el tema de la aplicación: Alidade Smooth en tema claro y Alidade Smooth Dark en oscuro (Stadia Maps con MapLibre GL). En local no necesita clave; en un dominio público hay que registrar el dominio en Stadia Maps o definir `STADIA_API_KEY` en `.env`.

## 5) Región

La simulación se desarrolla en Santiago de Compostela; la cabecera muestra la región activa. Hospitales, gasolineras y bases son los reales de la ciudad.

## 6) Mapa (`/map`)

Vista principal del gemelo digital con mapa interactivo:

- **Mapa interactivo** con ambulancias, emergencias, hospitales, gasolineras, estaciones de carga, estaciones meteorológicas, eventos externos y atascos en tiempo real.
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

## 7) Flota (`/fleet`)

Tarjetas de todas las unidades con indicadores de:

- Estado FSM (idle, en_route, on_scene, transporting, at_hospital, refueling/charging).
- Combustible (combustion) o batería (electric) según `powertrain` del tipo.
- Velocidad, RPM, motor health.
- Vitales del paciente (si está transportando) + severidad.
- Filtros por tipo, estado, severidad, energía baja.

Click en una tarjeta para detalles ampliados.

## 8) Panorama (`/overview`)

Resumen agregado de la región activa (datos de `GET /api/region/summary`):

- Agregados meteorológicos (avg temp, max precip, max viento, min visibilidad).
- Eventos externos activos por tipo y severidad (ver [Fuente de eventos](../technical/fuente-de-eventos.md)).
- KPIs de flota (total, emergencias activas, fuel low, ETA media, pulse rate).
- Weather impact: `worstFactor`, `avgFactor`, `affectedMissions`.
- Reparto por cuadrantes (NW/NE/SW/SE) alrededor del centro de la región.

## 9) Comunicaciones (`/comms`)

Para mantener el contacto con la flota:

- **Resumen**: canal en uso, unidades con enlace, latencia media y mensajes sin leer.
- **Enlace por unidad**: red (5G, 4G o 3G según la zona), calidad de señal, latencia, pérdida de paquetes y último contacto. Las unidades sin contacto o con señal débil aparecen primero; pulsa una fila para escribirle.
- **Mensajes a las unidades**: a una unidad o a toda la flota, con frases rápidas («Confirme su posición», «Regrese a la base»…). Cada mensaje muestra si está *en cola* (sin canal o sin cobertura), *entregado* o *leído*. El conductor lo ve en el panel del vehículo y confirma con «Recibido».
- **Canales de datos**: principal (MQTT), entre unidades (P2P) y respaldo (HTTP). Puedes apagarlos para comprobar que la flota sigue conectada por el siguiente; con todos apagados, los mensajes esperan en cola.

## 10) Panel de IA (con aprobación / autónoma)

Esquina inferior derecha:

- **Sugerencias de la IA**: panel con las anomalías detectadas, el protocolo sugerido y el razonamiento del LLM.
- **Botones**: aprobar / rechazar.
- **Modo "Con aprobación"**: la IA propone y el operador decide.
- **Modo "Autónoma"**: la IA ejecuta sin aprobación. Al activarlo, las pendientes se resuelven solas.
- **Log**: historial reciente de propuestas resueltas.

### Tipos de anomalías detectadas

| Anomalía | Trigger | Acción propuesta |
|----------|---------|------------------|
| Combustible bajo | fuel < 15% | Redirigir a gasolinera (combustion) |
| Batería baja | battery < 15% | Redirigir a charging station (electric) |
| Vitales críticas | SpO2 < 85% o HR > 140 | Redirigir al hospital más cercano |
| Emergencia desatendida | Sin asignar > 30s | Auto-despachar la unidad más cercana |
| Weather hazard | Eventos meteorológicos cerca | Reroute o pausa según severidad |

## 11) Asistente (Preguntar / Dar una orden)

Chat flotante (esquina inferior izquierda) con dos modos:

- **Preguntar** (RAG sobre protocolos): ej. "¿Cuál es el protocolo de IAM?".
- **Dar una orden** (tool-calling): ej. "muéstrame las ambulancias con combustible bajo", "centra el mapa en AMB-003", "pasa la IA a modo autónomo". Usa un fast-path por regex y el LLM local (Ollama) como fallback.

Detalles en [Chatbot IA](chatbot-ia.md).

## 12) Informes (`/reports`)

Generador LLM de informe operativo:

- Selecciona ventana (default 60 min, máx 24 h).
- Genera con `POST /api/ai/shift-report` → KPIs + highlights + recomendaciones.
- Listado histórico vía `GET /api/ai/shift-reports`.

## 13) Ajustes (`/config`)

- Catálogo de tipos de unidad y de lugar que aparecen en el mapa: crear, editar y borrar.
- Configuración de cómo se asignan las emergencias a las unidades.

## 14) Panel del vehículo (`/vehicle`, rol `vehicle`)

- Selector de tipo de unidad en el primer login (combustión, eléctrica, helicóptero, etc.).
- Mapa con ruta OSRM steps turn-by-turn (proxy `/api/osrm/...`).
- Telemetría reducida: posición, ETA, próxima maniobra.
- Botón "He llegado" para avanzar fase de misión manualmente.
- Modo manual (drag pin) o GPS real (`navigator.geolocation`).

## 15) Solución de problemas

- Backend caído → la UI muestra aviso de desconexión y cae a polling `/api/sim/state` cada 2 s.
- Sin persistencia → revisar `VITE_SUPABASE_URL` y `VITE_SUPABASE_ANON_KEY`.
- OSRM no responde → ambulancias usan ruta recta como fallback (badge OSRM en rojo).
- Sin eventos externos ni meteorología → revisar `EVENT_SOURCE` y el estado en `GET /api/events/status`; la simulación local sigue funcionando.
- El asistente tarda o no responde → comprobar que `ollama-init` terminó y el perfil de GPU usado; ver [IA local con Ollama](../technical/ai-chatbot-rag.md#ia-local-con-ollama).
- Recuperación de contraseña Supabase → revisar redirect URLs en Supabase Auth y abrir el enlace en el mismo navegador.

## Referencias

- [Supabase Local Development](https://supabase.com/docs/guides/local-development)
- [OSRM API](http://project-osrm.org/docs/v5.24.0/api/)
- [Runbook resiliencia operativa](../technical/runbook-resiliencia-operativa.md)
