-- Informes post-turno generados por IA: resumen ejecutivo de una ventana
-- temporal con KPIs, highlights (emergencias destacadas, anomalías, decisiones
-- IA) y recomendaciones. Útil para briefing de cambio de turno.

create table if not exists public.shift_reports (
  id            uuid primary key default gen_random_uuid(),
  started_at    timestamptz not null,
  ended_at      timestamptz not null,
  window_minutes int not null,
  kpis          jsonb not null default '{}'::jsonb,
  summary       text not null default '',
  highlights    jsonb not null default '[]'::jsonb,
  recommendations jsonb not null default '[]'::jsonb,
  generated_by  text not null default 'ai',
  created_at    timestamptz not null default now()
);

create index if not exists shift_reports_created_at_idx
  on public.shift_reports (created_at desc);
