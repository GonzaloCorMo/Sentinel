# Motor de IA — HITL y Modo Autonomo

## Resumen

El motor de IA de HPE Sentinel monitorea continuamente la simulacion para detectar anomalias y proponer acciones correctivas. Opera en dos modos:

- **HITL (Human-in-the-Loop)**: la IA propone acciones que el operador debe aprobar o rechazar.
- **Autonomo**: la IA ejecuta acciones directamente sin intervencion humana.

## Arquitectura

```
simulation/app/ai_decision_engine.py
        │
        ├─ Deteccion de anomalias (cada tick)
        │   ├─ low_fuel       → combustible < 15%
        │   ├─ vitals_critical → SpO2 < 85% o HR > 140
        │   └─ unattended_emergency → sin asignar > 30s
        │
        ├─ Busqueda de protocolo (RAG)
        │   └─ match_protocols() via Supabase RPC
        │
        ├─ Razonamiento LLM
        │   └─ chat_completion() → analisis y recomendacion
        │
        └─ Creacion de propuesta
            ├─ Modo HITL → estado "pending", espera aprobacion
            └─ Modo Autonomo → estado "auto_approved", ejecucion inmediata
```

## Deteccion de anomalias

El motor analiza el estado de cada ambulancia en cada tick:

### Combustible bajo (`low_fuel`)

- **Trigger**: fuel_level < 15%
- **Accion**: redirigir a la gasolinera mas cercana
- **Cooldown**: 60 segundos entre propuestas para la misma ambulancia

### Vitales criticas (`vitals_critical`)

- **Trigger**: SpO2 < 85% o frecuencia cardiaca > 140 bpm
- **Accion**: redirigir al hospital mas cercano
- **Cooldown**: 60 segundos

### Emergencia desatendida (`unattended_emergency`)

- **Trigger**: emergencia pendiente sin ambulancia asignada durante > 30 segundos
- **Accion**: auto-despachar la ambulancia disponible mas cercana
- **Cooldown**: 90 segundos

## Pipeline de decision

1. **Detectar anomalia** en los datos de telemetria.
2. **Generar embedding** de la descripcion de la anomalia.
3. **Buscar protocolos** relevantes en `protocols_knowledge` via `match_protocols()`.
4. **Solicitar razonamiento** al LLM con el contexto del protocolo y los datos de telemetria.
5. **Crear propuesta** con la anomalia, protocolo sugerido y razonamiento del LLM.

## Modos de operacion

### HITL (Human-in-the-Loop)

- La propuesta se crea con estado `pending`.
- Se envia al frontend via SSE (`/api/sim/stream`) y se lista en `GET /api/ai/proposals`.
- El operador ve la propuesta en el `AIProposalPanel`.
- Puede **aprobar** (la accion se ejecuta) o **rechazar** (se descarta).
- Endpoint: `POST /api/ai/proposals/{proposal_id}/resolve` con `{ action: "approved" | "rejected" }`.

### Autonomo

- La propuesta se crea con estado `auto_approved`.
- La accion se ejecuta inmediatamente sin esperar aprobacion.
- El operador ve el log de acciones ejecutadas en el panel IA.
- Configurable en tiempo real via `POST /api/ai/mode` con `{ mode: "autonomous" }`.
- **Importante**: al pasar a autónomo, las propuestas pendientes que estaban en HITL se auto-resuelven (`approved`) inmediatamente. El motor deja de auto-asignar ambulancias a emergencias — solo la IA controla el despacho.

## Perfiles de severidad de pacientes

Cuando una ambulancia recoge a un paciente, se le asigna aleatoriamente una severidad:

| Severidad | Probabilidad | Efecto en telemetria |
|-----------|-------------|----------------------|
| **Stable** | 50% | Vitales normales, fluctuaciones minimas |
| **Moderate** | 30% | Vitales alteradas (HR elevada, SpO2 reducida, PA alta) |
| **Critical** | 20% | Vitales extremas (taquicardia, hipoxia, hipertension severa) |

Los perfiles afectan a: frecuencia cardiaca, presion arterial, SpO2, GCS, EtCO2, glucosa, temperatura, frecuencia respiratoria y ritmo ECG.

## Persistencia

### Tabla `ai_hitl_proposals`

| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `id` | uuid | PK |
| `ambulance_id` | text | Ambulancia afectada |
| `anomaly_type` | text | Tipo de anomalia |
| `description` | text | Descripcion legible |
| `protocol_snippet` | text | Protocolo sugerido |
| `llm_reasoning` | text | Razonamiento del LLM |
| `status` | text | pending/approved/rejected/auto_approved |
| `created_at` | timestamptz | Creacion |
| `resolved_at` | timestamptz | Resolucion |

## Endpoints

| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET | `/api/ai/mode` | Consultar modo actual |
| POST | `/api/ai/mode` | Cambiar modo (`hitl` / `autonomous`) |
| GET | `/api/ai/proposals` | Lista de propuestas pendientes |
| POST | `/api/ai/proposals/{proposal_id}/resolve` | Aprobar (`approved`) o rechazar (`rejected`) |
| GET | `/api/sim/dispatch-config` | `{dispatchRequiresApproval: bool}` |
| POST | `/api/sim/dispatch-config` | Activa/desactiva HITL para nuevas emergencias |
| POST | `/api/ai/command` | Comando NL → tool-calling estructurado (filter_units, focus_unit, set_ai_mode, reset_filters) |
| POST | `/api/ai/shift-report` | Genera informe operativo LLM de los últimos N min |
| GET | `/api/ai/shift-reports?limit` | Lista informes guardados |

## Frontend

### `AIProposalPanel.vue`

Panel colapsable en la esquina inferior derecha:

- Lista de propuestas pendientes con indicador de urgencia.
- Razonamiento del LLM en formato blockquote.
- Botones de aprobar/rechazar.
- Badge de modo (HITL/Auto) en el header.
- Log de propuestas resueltas recientemente.
