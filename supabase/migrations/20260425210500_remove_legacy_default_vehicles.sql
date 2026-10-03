-- Elimina tipos de vehículo legacy previos al catálogo por powertrain.
-- Mantiene lugares builtin (hospital, gas_station) y el nuevo catálogo.

DELETE FROM public.fleet_entity_types
WHERE id IN ('ambulance', 'helicopter', 'police_patrol');
