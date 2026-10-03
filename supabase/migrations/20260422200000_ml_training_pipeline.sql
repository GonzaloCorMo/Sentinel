-- ============================================================================
-- Pipeline de datos para entrenamiento de modelos propios.
-- ============================================================================
-- Añade 3 tablas que capturan lifecycle + outcomes + feature snapshots de las
-- decisiones de despacho. Diseñadas para export masivo a Parquet y consumo
-- por el proyecto paralelo hpe-ml-training/.
--
-- Referencias de diseño:
--  - JSONB flexible > tabla-por-tipo: https://www.postgresql.org/docs/current/datatype-json.html
--  - Event sourcing pattern:         https://martinfowler.com/eaaDev/EventSourcing.html
--  - Feature stores idea base:       https://www.featurestore.org/what-is-a-feature-store
-- ============================================================================

-- ── entity_events ───────────────────────────────────────────────────────────
-- Un único log append-only para el ciclo de vida de TODO objeto del mapa:
-- unidades, companions, POIs, emergencias, jams. `kind` discrimina el tipo,
-- `event_type` la acción, `payload` mantiene los campos específicos del tipo
-- como JSONB (evita migraciones al añadir nuevos entity_types custom).
create table if not exists public.entity_events (
    id           bigserial primary key,
    ts           timestamptz not null default now(),
    kind         text        not null,   -- ambulance | companion | poi | emergency | jam | entity_type
    entity_id    text        not null,   -- UUID del objeto (o short-id para entity_type)
    event_type   text        not null,   -- created | updated | deleted | dispatched | resolved | rerouted | phase_change
    actor        text,                   -- operator | ai_observer | engine | citizen | vehicle
    payload      jsonb       not null default '{}'::jsonb,
    tick         bigint,                 -- tick del motor al emitir
    simulation_session_id uuid           -- agrupa eventos de la misma ejecución (reset → nuevo id)
);

create index if not exists idx_entity_events_ts         on public.entity_events (ts desc);
create index if not exists idx_entity_events_kind_type  on public.entity_events (kind, event_type);
create index if not exists idx_entity_events_entity     on public.entity_events (entity_id);
create index if not exists idx_entity_events_session    on public.entity_events (simulation_session_id);
create index if not exists idx_entity_events_payload    on public.entity_events using gin (payload);


-- ── mission_outcomes ────────────────────────────────────────────────────────
-- Una fila por emergencia resuelta (o cancelada) con los KPIs que el modelo
-- ETA / dispatch ranker necesita como labels.
create table if not exists public.mission_outcomes (
    id                       bigserial primary key,
    emergency_id             text        not null,
    ambulance_id             text,
    created_at               timestamptz not null default now(),
    dispatched_at            timestamptz,
    arrived_on_scene_at      timestamptz,
    arrived_at_hospital_at   timestamptz,
    resolved_at              timestamptz,

    -- Tiempos derivados (segundos)
    response_time_s          real,        -- dispatched → arrived_on_scene
    transport_time_s         real,        -- arrived_on_scene → arrived_at_hospital
    total_time_s             real,        -- created → resolved

    -- ETA predicho por el motor al despachar vs real. Label clave para regressor.
    eta_predicted_s          real,
    eta_error_s              real,        -- real - predicted (signo mantiene si llegó antes/después)

    -- Recursos consumidos
    trip_distance_m          real,
    fuel_burn_pct            real,
    battery_drain_pct        real,

    -- Contexto
    emergency_type           text,        -- medical | altercation | mass_casualty | ...
    patient_severity         text,        -- stable | moderate | critical (al entregar)
    jams_crossed             int,         -- nº polígonos atasco que cruzó la ruta
    rerouted_times           int default 0,
    companions_dispatched    text[]       default '{}', -- ['helicopter','police_patrol']

    -- Calidad de decisión (para rankers futuros)
    outcome_quality          real,        -- score compuesto 0..1 (menor ETA, menos jams, etc.)
    simulation_session_id    uuid
);

create index if not exists idx_mission_outcomes_emergency on public.mission_outcomes (emergency_id);
create index if not exists idx_mission_outcomes_type      on public.mission_outcomes (emergency_type);
create index if not exists idx_mission_outcomes_session   on public.mission_outcomes (simulation_session_id);
create index if not exists idx_mission_outcomes_created   on public.mission_outcomes (created_at desc);


-- ── route_decisions ─────────────────────────────────────────────────────────
-- Snapshot de features del estado del mundo EN EL INSTANTE de una decisión
-- de despacho (o auto-assign). Sirve para learning-to-rank: dado el estado
-- de la flota + emergencia, ¿qué unidad es la mejor?
create table if not exists public.route_decisions (
    id                    bigserial primary key,
    ts                    timestamptz not null default now(),
    emergency_id          text        not null,
    chosen_ambulance_id   text,
    candidate_count       int         not null default 0,

    -- Contexto emergencia
    emergency_type        text,
    emergency_lat         real,
    emergency_lon         real,
    description_embedding vector(1536),   -- reutiliza pgvector ya instalado

    -- Snapshot flota compacto: array de dicts {id,lat,lon,fuel,battery,phase,eta_s,distance_m}
    candidates            jsonb       not null default '[]'::jsonb,

    -- Decisión + fuente
    decision_source       text        not null, -- engine_auto | ai_autonomous | ai_hitl | operator
    decided_by            text,                 -- user_id o null
    proposal_id           text,                 -- si vino de ai_hitl_proposals

    -- Join con mission_outcomes (label) — se resuelve tras completar.
    outcome_id            bigint references public.mission_outcomes(id) on delete set null,

    simulation_session_id uuid
);

create index if not exists idx_route_decisions_ts           on public.route_decisions (ts desc);
create index if not exists idx_route_decisions_emergency    on public.route_decisions (emergency_id);
create index if not exists idx_route_decisions_source       on public.route_decisions (decision_source);
create index if not exists idx_route_decisions_session      on public.route_decisions (simulation_session_id);
create index if not exists idx_route_decisions_candidates   on public.route_decisions using gin (candidates);


-- ── Vista de features ready-to-train ────────────────────────────────────────
-- Combina mission_outcomes + route_decisions para obtener un dataset listo
-- para XGBoost: X = features del estado, y = response_time_s / eta_error_s.
create or replace view public.ml_mission_training as
select
    mo.id                      as outcome_id,
    mo.emergency_id,
    mo.ambulance_id,
    mo.emergency_type,
    mo.patient_severity,
    mo.response_time_s,
    mo.transport_time_s,
    mo.total_time_s,
    mo.eta_predicted_s,
    mo.eta_error_s,
    mo.trip_distance_m,
    mo.fuel_burn_pct,
    mo.jams_crossed,
    mo.rerouted_times,
    mo.outcome_quality,
    mo.simulation_session_id,
    mo.created_at,
    rd.candidate_count,
    rd.decision_source,
    rd.emergency_lat,
    rd.emergency_lon,
    rd.candidates              as fleet_snapshot
from public.mission_outcomes mo
left join public.route_decisions rd on rd.outcome_id = mo.id
where mo.response_time_s is not null;


-- ── simulation_sessions ─────────────────────────────────────────────────────
-- Registra cada sesión (arranque / reset) para particionar datasets.
create table if not exists public.simulation_sessions (
    id            uuid primary key,
    started_at    timestamptz not null default now(),
    ended_at      timestamptz,
    mode          text        not null default 'interactive', -- interactive | training_autonomous
    speed_multiplier real,
    notes         text,
    total_ticks   bigint,
    total_emergencies int default 0,
    total_resolved int default 0
);

create index if not exists idx_sim_sessions_started on public.simulation_sessions (started_at desc);
create index if not exists idx_sim_sessions_mode    on public.simulation_sessions (mode);


-- ── Grant service_role explícito (RLS no aplica en service role, pero por si)
-- No hay policies: estas tablas son internas del backend.
