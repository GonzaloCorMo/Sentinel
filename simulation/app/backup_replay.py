"""Backup mode: reproduce eventos + clima históricos de un día desde Kafka.

Lanza un consumer temporal (group_id único, ``earliest``,
``enable_auto_commit=false``), itera ``aruba.events`` + ``aruba.weather``,
filtra por timestamp dentro del día elegido (UTC) y los ingesta en el engine
vía ``ingest_aruba_event`` / ``ingest_aruba_weather``. Dedupe por id ya está
garantizado en el engine, así que ejecutar el mismo día varias veces no
produce duplicados.

Stop por:
  - Llegar al fin del topic (poll vacío durante ``IDLE_DEADLINE_S``).
  - Encontrar mensaje posterior al fin del día (asume orden cronológico
    aproximado del broker).
  - Cancelación explícita vía ``/api/sim/backup/stop``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import date, datetime, time, timezone
from typing import Any

from .events_consumer import EventsConsumerConfig, _build_consumer_kwargs, load_events_consumer_config
from .schemas.aruba_events import parse_event, parse_weather

log = logging.getLogger(__name__)

# Si tras este tiempo sin mensajes no hay nada, asumimos fin del topic.
IDLE_DEADLINE_S = 8.0
# Cap defensivo para no procesar millones de mensajes en runs accidentales.
MAX_MESSAGES = 100000
MIN_DATE = date(2026, 4, 1)


def _parse_iso(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


async def replay_aruba_day(
    engine: Any,
    day: date,
    *,
    cfg: EventsConsumerConfig | None = None,
    show_all: bool = True,
    natural_speed: float = 60.0,
) -> dict[str, Any]:
    """Reproduce mensajes de ambos topics cuyo timestamp cae en `day` (UTC).

    Args:
        show_all: True (default) ⇒ ingest instantáneo, todo aparece de golpe.
            False ⇒ respeta deltas temporales entre mensajes para que aparezcan
            "de forma natural" (acelerado por `natural_speed`).
        natural_speed: factor temporal cuando `show_all=False`. 60 = 1 min real
            por hora simulada. Tope máximo de espera entre mensajes: 30s.

    Devuelve estado final (también persistido en `engine.backup_status`).
    """
    if cfg is None:
        cfg = load_events_consumer_config()
    if not cfg.enabled or (not cfg.topic_events and not cfg.topic_weather):
        engine.set_backup_status({
            "running": False,
            "error": "kafka_disabled",
            "day": day.isoformat(),
            "scanned": 0,
            "ingestedEvents": 0,
            "ingestedWeather": 0,
            "finishedAt": _iso_now(),
        })
        return engine.backup_status

    if day < MIN_DATE:
        engine.set_backup_status({
            "running": False,
            "error": f"day_before_min ({MIN_DATE.isoformat()})",
            "day": day.isoformat(),
            "scanned": 0,
            "ingestedEvents": 0,
            "ingestedWeather": 0,
            "finishedAt": _iso_now(),
        })
        return engine.backup_status

    day_start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    day_end = datetime.combine(day, time.max, tzinfo=timezone.utc)

    # Consumer dedicado: group_id único + sin commit, para no afectar al
    # consumer en vivo. Reset earliest para empezar desde el principio.
    kwargs = _build_consumer_kwargs(cfg)
    kwargs["group_id"] = f"backup-replay-{uuid.uuid4().hex[:10]}"
    kwargs["auto_offset_reset"] = "earliest"
    kwargs["enable_auto_commit"] = False

    from aiokafka import AIOKafkaConsumer  # local: opcional

    topics = [t for t in (cfg.topic_events, cfg.topic_weather) if t]
    consumer = AIOKafkaConsumer(*topics, **kwargs)

    state: dict[str, Any] = {
        "running": True,
        "day": day.isoformat(),
        "showAll": bool(show_all),
        "naturalSpeed": float(natural_speed) if not show_all else None,
        "scanned": 0,
        "ingestedEvents": 0,
        "ingestedWeather": 0,
        "skippedBefore": 0,
        "skippedAfter": 0,
        "skippedInvalid": 0,
        "topics": list(topics),
        "error": None,
        "startedAt": _iso_now(),
        "finishedAt": None,
    }
    engine.set_backup_status(dict(state))

    try:
        await consumer.start()
        engine._backup_replay_active = True
        log.info(
            "Backup replay started day=%s topics=%s", day.isoformat(), topics,
        )

        cancel_token = engine._backup_cancel_token
        # Conta cuántas particiones de cada topic ya superaron `day_end` para
        # parar pronto si el broker está ordenado cronológicamente.
        stop_after_batch = False
        prev_ts: datetime | None = None
        speed = max(1.0, float(natural_speed))
        max_sleep = 30.0

        while True:
            if engine._backup_cancel_token != cancel_token:
                state["error"] = "cancelled"
                break
            if state["scanned"] >= MAX_MESSAGES:
                state["error"] = "max_messages_reached"
                break
            try:
                batch = await asyncio.wait_for(
                    consumer.getmany(timeout_ms=int(IDLE_DEADLINE_S * 1000), max_records=200),
                    timeout=IDLE_DEADLINE_S + 2.0,
                )
            except asyncio.TimeoutError:
                break
            if not batch:
                break
            empty = True
            for tp, messages in batch.items():
                topic = tp.topic
                for msg in messages:
                    empty = False
                    state["scanned"] += 1
                    if topic == cfg.topic_events:
                        parsed = parse_event(msg.value)
                        if parsed is None:
                            state["skippedInvalid"] += 1
                            continue
                        ts = _parse_iso(parsed.started_at)
                        if ts is None:
                            state["skippedInvalid"] += 1
                            continue
                        if ts < day_start:
                            state["skippedBefore"] += 1
                            continue
                        if ts > day_end:
                            state["skippedAfter"] += 1
                            stop_after_batch = True
                            continue
                        if not show_all and prev_ts is not None:
                            delta = (ts - prev_ts).total_seconds() / speed
                            if delta > 0:
                                await asyncio.sleep(min(delta, max_sleep))
                        prev_ts = ts
                        await engine.ingest_aruba_event(parsed.model_dump())
                        state["ingestedEvents"] += 1
                    elif topic == cfg.topic_weather:
                        parsed_w = parse_weather(msg.value)
                        if parsed_w is None:
                            state["skippedInvalid"] += 1
                            continue
                        ts = _parse_iso(parsed_w.timestamp)
                        if ts is None:
                            state["skippedInvalid"] += 1
                            continue
                        if ts < day_start:
                            state["skippedBefore"] += 1
                            continue
                        if ts > day_end:
                            state["skippedAfter"] += 1
                            stop_after_batch = True
                            continue
                        if not show_all and prev_ts is not None:
                            delta = (ts - prev_ts).total_seconds() / speed
                            if delta > 0:
                                await asyncio.sleep(min(delta, max_sleep))
                        prev_ts = ts
                        await engine.ingest_aruba_weather(parsed_w.model_dump())
                        state["ingestedWeather"] += 1
                    else:
                        log.debug("Backup replay: topic inesperado %s", topic)
                engine.set_backup_status(dict(state))
            if stop_after_batch or empty:
                break
    except asyncio.CancelledError:
        state["error"] = "cancelled"
        raise
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
        log.exception("Backup replay failed")
    finally:
        engine._backup_replay_active = False
        try:
            await consumer.stop()
        except Exception:
            pass
        state["running"] = False
        state["finishedAt"] = _iso_now()
        engine.set_backup_status(dict(state))
    return state


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
