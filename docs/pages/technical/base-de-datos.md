# Base de datos (Supabase relacional + vectorial)

## Objetivo

Este documento describe el modelo de datos del gemelo digital HPE Sentinel, incluyendo tablas relacionales para telemetria y tablas vectoriales para IA.

## Extensiones habilitadas

- `pgvector` — soporte de vectores para embeddings y busqueda semantica
- `uuid-ossp` — generacion de UUIDs

## Tablas principales

### `telemetry_logs`

Registro de telemetria por tick de simulacion. Usa `bigint GENERATED ALWAYS AS IDENTITY` como clave primaria para rendimiento en inserciones masivas.

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | bigint (identity) | Clave primaria |
| `ambulance_id` | text | ID de la ambulancia |
| `timestamp` | timestamptz | Momento del registro |
| `medical_data` | jsonb | Vitales del paciente |
| `mechanical_data` | jsonb | Estado mecanico del vehiculo |
| `gps_data` | jsonb | Coordenadas y velocidad |

Indice B-Tree compuesto sobre `(ambulance_id, timestamp)`.

### `ai_hitl_proposals`

Propuestas generadas por el motor de IA.

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | Clave primaria |
| `ambulance_id` | text | Ambulancia afectada |
| `anomaly_type` | text | Tipo: `low_fuel`, `vitals_critical`, `unattended_emergency` |
| `description` | text | Descripcion legible |
| `protocol_snippet` | text | Fragmento del protocolo sugerido |
| `llm_reasoning` | text | Razonamiento generado por el LLM |
| `status` | text | `pending`, `approved`, `rejected`, `auto_approved` |
| `created_at` | timestamptz | Fecha de creacion |
| `resolved_at` | timestamptz | Fecha de resolucion |

### `protocols_knowledge`

Protocolos operativos con embeddings para busqueda semantica (decision HITL).

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | Clave primaria |
| `category` | text | Categoria del protocolo |
| `content` | text | Contenido textual |
| `embedding` | vector(1536) | Embedding vectorial |

Indice IVFFlat sobre `embedding` con distancia coseno.

### `ai_knowledge_chunks`

Base de conocimiento del chatbot para RAG.

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | Clave primaria |
| `title` | text | Titulo del fragmento |
| `content` | text | Contenido textual |
| `metadata` | jsonb | Metadatos adicionales |
| `embedding` | vector(1536) | Embedding vectorial |

### `chat_messages`

Historial de conversaciones del chatbot.

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | Clave primaria |
| `session_id` | text | ID de sesion del chat |
| `role` | text | `user` o `assistant` |
| `content` | text | Contenido del mensaje |
| `created_at` | timestamptz | Fecha de creacion |

### `fleet_entity_types`

Catálogo de tipos de unidad (built-in + custom + IA-generadas). Sembrado al arrancar.

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | text | PK (ej. `ambulance_combustion`, `helicopter`, `police_car`) |
| `name` | text | Nombre legible |
| `kind` | text | `vehicle`, `companion`, `place` |
| `built_in` | bool | True para tipos del sistema |
| `powertrain` | text | `combustion`, `electric`, `unique` |
| `speed_kmh` | float | Velocidad nominal |
| `crew_min` / `crew_max` | int | Tripulación |
| `cost_per_min` / `activation_cost` | float | Coste operativo |
| `description` | text | Generada por LLM si falta |
| `capabilities` | text[] | Lista de capacidades (RAG-friendly) |
| `created_at` / `updated_at` | timestamptz | Auditoría |

### `fleet_vehicles`

Unidades persistidas registradas por usuarios `vehicle`.

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | PK |
| `owner_user_id` | uuid | FK al user (auth) |
| `entity_type_id` | text | FK a `fleet_entity_types` |
| `display_label` | text | Etiqueta visible (ej. `AMB-001`) |
| `last_lat` / `last_lon` | float | Última posición conocida |
| `created_at` / `updated_at` | timestamptz | Auditoría |

### `ai_shift_reports`

Informes post-turno generados por el LLM flagship.

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | PK |
| `window_minutes` | int | Ventana cubierta |
| `generated_at` | timestamptz | Timestamp |
| `summary` | text | Resumen ejecutivo |
| `kpis` | jsonb | KPIs cuantitativos |
| `highlights` | jsonb | Eventos destacados |
| `recommendations` | jsonb | Acciones sugeridas |

### `ml_predictions`

Resultados del modelo `fleet_anomaly` (IsolationForest ONNX).

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | PK |
| `ambulance_id` | text | Unidad |
| `predicted_at` | timestamptz | Timestamp |
| `score` | float | Score anomalía (más negativo = más anómalo) |
| `is_anomaly` | bool | Threshold aplicado |
| `features` | jsonb | Features usadas |

### `ml_telemetry_features` (vista)

Vista materializada para training: 51 columnas derivadas de `telemetry_logs` + scoring derivados (vehicle health, clinical risk, etc.). Usada por `GET /api/ml/export`.

### `simulation_sessions`

Particiona el dataset ML por sesión de training.

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | PK |
| `started_at` / `ended_at` | timestamptz | Ventana |
| `mode` | text | `interactive`, `training_autonomous`, `backup_replay` |
| `notes` | jsonb | Contexto adicional |

## Funciones RPC

### `match_protocols`

Busca protocolos por similitud coseno con un embedding de consulta.

```sql
match_protocols(
  query_embedding vector(1536),
  match_threshold float,
  match_count int
) RETURNS TABLE (id uuid, category text, content text, similarity float)
```

### `match_ai_knowledge_chunks`

Busca fragmentos de conocimiento por similitud para el chatbot RAG.

```sql
match_ai_knowledge_chunks(
  query_embedding vector(1536),
  match_count int,
  filter jsonb
) RETURNS TABLE (id uuid, title text, content text, metadata jsonb, similarity float)
```

## Relacion con el codigo

### Backend (Python / FastAPI)

- Escritura de telemetria: `simulation/app/engine.py` → insercion batch en `telemetry_logs` cada tick
- Propuestas IA: `simulation/app/ai_decision_engine.py` → insercion en `ai_hitl_proposals`
- Chatbot RAG: `simulation/app/chat_service.py` → consulta `ai_knowledge_chunks` via RPC
- Protocolos: `simulation/app/ai_decision_engine.py` → consulta `protocols_knowledge` via RPC

### Frontend (Vue 3)

- Auth: `frontend/src/lib/supabase.ts` → autenticacion via Supabase client
- Estado de simulacion: recibido via SSE, no consulta directa a la BD

## Flujo de datos

1. Motor de simulacion genera telemetria cada tick.
2. FastAPI inserta batch en `telemetry_logs`.
3. Motor IA consulta `telemetry_logs` para detectar anomalias.
4. Al detectar anomalia, busca en `protocols_knowledge` via RPC y genera propuesta con LLM.
5. Propuesta se inserta en `ai_hitl_proposals` y se envia al frontend via SSE.
6. Usuario aprueba/rechaza en el panel IA → se actualiza `ai_hitl_proposals`.

## Referencias

- [Supabase Database](https://supabase.com/docs/guides/database/overview)
- [pgvector](https://github.com/pgvector/pgvector)
- [PostgreSQL JSONB](https://www.postgresql.org/docs/current/datatype-json.html)
