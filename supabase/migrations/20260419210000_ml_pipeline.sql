-- Pipeline ML: columnas jsonb nuevas en telemetry_logs (environmental,
-- network, derived, meta) + vista feature-store aplanada + tabla predicciones.

alter table public.telemetry_logs
  add column if not exists environmental_data jsonb,
  add column if not exists network_data jsonb,
  add column if not exists derived_data jsonb,
  add column if not exists meta jsonb;

-- Vista feature-store: una fila por tick con columnas aplanadas listas para ML.
-- Los consumidores (exporter Parquet, pandas, modelo ONNX) la tratan como tabla.
create or replace view public.ml_telemetry_features as
select
  tl.id,
  tl.ambulance_id,
  tl.timestamp,
  (tl.gps_data ->> 'speedKmh')::double precision                    as gps_speed_kmh,
  (tl.gps_data ->> 'speedMs')::double precision                     as gps_speed_ms,
  (tl.gps_data ->> 'headingDeg')::double precision                  as gps_heading_deg,
  (tl.gps_data ->> 'altitudeM')::double precision                   as gps_altitude_m,
  (tl.gps_data ->> 'gpsHdop')::double precision                     as gps_hdop,
  (tl.gps_data ->> 'satellitesUsed')::integer                       as gps_satellites,
  (tl.gps_data ->> 'jerkMs3')::double precision                     as gps_jerk_ms3,
  (tl.mechanical_data ->> 'fuelLevelPct')::double precision         as mech_fuel_pct,
  (tl.mechanical_data ->> 'batteryPct')::double precision           as mech_battery_pct,
  (tl.mechanical_data ->> 'engineTempC')::double precision          as mech_engine_temp_c,
  (tl.mechanical_data ->> 'engineRpm')::double precision            as mech_engine_rpm,
  (tl.mechanical_data ->> 'avgConsumptionL100km')::double precision as mech_consumption,
  (tl.mechanical_data ->> 'rangeKm')::double precision              as mech_range_km,
  (tl.mechanical_data ->> 'oilPressureBar')::double precision       as mech_oil_pressure_bar,
  (tl.mechanical_data ->> 'engineHealthPct')::double precision      as mech_engine_health,
  (tl.mechanical_data ->> 'ecoScore')::double precision             as mech_eco_score,
  (tl.mechanical_data ->> 'longitudinalG')::double precision        as mech_long_g,
  (tl.mechanical_data ->> 'lateralG')::double precision             as mech_lat_g,
  (tl.mechanical_data ->> 'engineVibrationG')::double precision     as mech_vibration_g,
  (tl.mechanical_data ->> 'powerState')                             as mech_power_state,
  (tl.mechanical_data ->> 'sirensActive')::boolean                  as mech_sirens,
  (tl.medical_data ->> 'heartRateBpm')::double precision            as med_hr_bpm,
  (tl.medical_data ->> 'spo2Pct')::double precision                 as med_spo2,
  (tl.medical_data ->> 'respiratoryRatePerMin')::integer            as med_resp_rate,
  (tl.medical_data ->> 'gcsScore')::integer                         as med_gcs,
  (tl.medical_data ->> 'bodyTempC')::double precision               as med_body_temp,
  (tl.medical_data ->> 'meanArterialPressureMmhg')::double precision as med_map_mmhg,
  (tl.medical_data ->> 'shockIndex')::double precision              as med_shock_index,
  (tl.medical_data ->> 'lactateMmolL')::double precision            as med_lactate,
  (tl.medical_data ->> 'news2Score')::integer                       as med_news2,
  (tl.medical_data ->> 'triageLevel')::integer                      as med_triage,
  (tl.environmental_data -> 'exterior' ->> 'tempC')::double precision    as env_ext_temp_c,
  (tl.environmental_data -> 'exterior' ->> 'visibilityKm')::double precision as env_ext_visibility_km,
  (tl.environmental_data -> 'cabin' ->> 'co2Ppm')::double precision       as env_cabin_co2,
  (tl.environmental_data -> 'cabin' ->> 'noiseDb')::double precision      as env_cabin_noise_db,
  (tl.environmental_data ->> 'chassisVibrationG')::double precision       as env_vibration_g,
  (tl.network_data ->> 'networkType')                               as net_type,
  (tl.network_data ->> 'rssiDbm')::double precision                 as net_rssi_dbm,
  (tl.network_data ->> 'packetLossPct')::double precision           as net_loss_pct,
  (tl.network_data -> 'latencyMs' ->> 'mqtt')::double precision     as net_lat_mqtt_ms,
  (tl.derived_data ->> 'vehicleHealthPct')::double precision        as der_vehicle_health,
  (tl.derived_data ->> 'linkQualityPct')::double precision          as der_link_quality,
  (tl.derived_data ->> 'cabinComfortPct')::double precision         as der_cabin_comfort,
  (tl.derived_data ->> 'drivingAggressionScore')::double precision  as der_aggression,
  (tl.derived_data ->> 'clinicalRiskScore')::double precision       as der_clinical_risk,
  jsonb_array_length(coalesce(tl.derived_data -> 'vehicleAlerts', '[]'::jsonb))  as der_vehicle_alerts_count,
  jsonb_array_length(coalesce(tl.derived_data -> 'clinicalAlerts', '[]'::jsonb)) as der_clinical_alerts_count,
  (tl.meta ->> 'schemaVersion')                                     as meta_schema_version,
  (tl.meta ->> 'tick')::integer                                     as meta_tick
from public.telemetry_logs tl;

-- Tabla de predicciones ML
create table if not exists public.ml_predictions (
  id            uuid primary key default gen_random_uuid(),
  ambulance_id  text not null,
  model_name    text not null,
  model_version text not null default 'v1',
  prediction    jsonb not null,
  features_in   jsonb,
  score         double precision,
  predicted_at  timestamptz not null default now()
);

create index if not exists ml_predictions_amb_ts_idx
  on public.ml_predictions (ambulance_id, predicted_at desc);
create index if not exists ml_predictions_model_idx
  on public.ml_predictions (model_name, predicted_at desc);
