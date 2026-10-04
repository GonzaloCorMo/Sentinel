-- Limpieza de tablas que ningún servicio lee ni escribe.
--
-- - ambulance_units, emergency_cases, emergency_dispatches: modelo relacional
--   inicial; el motor guarda flota y emergencias en memoria y su historial en
--   entity_events / mission_outcomes.
-- - simulation_snapshots, saved_scenarios: nunca se llegaron a usar.
-- - operation_events: la leía el informe de turno, pero nada escribía en ella;
--   el informe usa ahora entity_events, la auditoría real del motor.
-- - Trigger INSTEAD OF de weather_stations_latest: el backend escribe
--   directamente en weather_readings; la vista sigue existiendo (solo lectura).
--
-- Ninguna vista depende de estas tablas y las únicas claves foráneas son
-- entre ellas mismas (emergency_dispatches → ambulance_units / emergency_cases).

drop table if exists public.emergency_dispatches;
drop table if exists public.emergency_cases;
drop table if exists public.ambulance_units;
drop table if exists public.simulation_snapshots;
drop table if exists public.saved_scenarios;
drop table if exists public.operation_events;

drop trigger if exists weather_latest_instead_of_trigger on public.weather_stations_latest;
drop function if exists public.weather_latest_instead_of();
