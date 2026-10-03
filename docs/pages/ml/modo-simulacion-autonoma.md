# Modo simulación autónoma

Activa un bucle que alimenta continuamente el pipeline ML sin intervención humana. Pensado para dejar corriendo el gemelo durante horas (o días) y generar el volumen de datos que los modelos necesitan.

## Qué hace

Cuando `trainingMode=true` el motor, cada tick:

1. Si faltan hospitales o gasolineras → crea 2 de cada automáticamente (coordenadas random alrededor del centro del mapa).
2. Si hay menos de 3 ambulancias → añade una (hasta reponer el mínimo).
3. Cada `60 / ratePerMin` segundos simulados → genera una emergencia random (medical / altercation / mass_casualty) en un radio alrededor del centro.
4. Fuerza `paused=False` y `dispatchRequiresApproval=False` para que el generador no quede atascado.
5. Abre una nueva `simulation_sessions` para etiquetar los datos capturados.

## Activar

```bash
curl -X POST http://localhost:8000/api/sim/training-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"ratePerMin":20}'
```

Respuesta:
```json
{
  "ok": true,
  "trainingMode": true,
  "rate": 20.0,
  "sessionId": "f17d7ac6-…"
}
```

## Desactivar

```bash
curl -X POST http://localhost:8000/api/sim/training-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled":false}'
```

## Combinar con speed multiplier

El `ratePerMin` se mide en **tiempo simulado**. Sube `speedMultiplier` para amplificar la generación real:

| speed | ratePerMin | Missions/hora real |
|---|---|---|
| 1 | 3 | 3 |
| 5 | 10 | 50 |
| 10 | 20 | 200 |
| 15 | 30 | 450 |

A partir de ~500 missions/h la BD empieza a notar el write load — monitoriza con:
```sql
select count(*), max(ts) from entity_events where ts > now() - interval '1 minute';
```

## Monitorización

Panel SQL simple mientras corre:
```sql
select
  date_trunc('minute', created_at) as minute,
  count(*) as missions,
  avg(response_time_s)::int as avg_resp_s,
  avg(eta_error_s)::int as avg_eta_err_s,
  avg(outcome_quality)::numeric(4,2) as avg_quality
from mission_outcomes
where created_at > now() - interval '1 hour'
group by 1
order by 1 desc;
```

## Parar + limpiar al terminar

```bash
# 1. Desactivar generador
curl -X POST http://localhost:8000/api/sim/training-mode -d '{"enabled":false}' -H "Content-Type: application/json"

# 2. Flush final (o esperar al próximo tick)
#    cierra sesión automáticamente al apagar el motor:
docker compose stop simulation && docker compose start simulation

# 3. Exportar dataset desde el proyecto paralelo
cd ../hpe-ml-training
python -m hpe_ml.export --out data/
```

## Avisos

- **No es compatible con HITL humano**: fuerza `dispatchRequiresApproval=false`.
- **Consumo de Supabase**: a rate altos la tabla `entity_events` crece rápido. Limpia periódicamente con `DELETE FROM entity_events WHERE ts < now() - interval '30 days'` si acumulas mucho.
- **Emergencias no resueltas**: si la flota es demasiado pequeña para el `ratePerMin`, acumulas pending. Sube la flota mínima (código: `_training_mode_tick` en `simulation/app/engine.py`).

## Campos expuestos en el estado del motor

Ahora `/api/sim/state` incluye:
```json
{
  "trainingMode": true,
  "trainingRatePerMin": 20.0,
  "sessionId": "f17d7ac6-..."
}
```

Útil para un toggle en el frontend (aún no implementado — `staticVehiclePalette` es un buen sitio para añadir botón "🤖 Auto").
