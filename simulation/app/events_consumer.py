"""Consumer Kafka para los topics de Aruba Pulse (`aruba.events` y
`aruba.weather`).

Esquema en `simulation/app/schemas/aruba_events.py`. La conexión reusa
las credenciales SASL del productor de telemetría (mismo broker en
`10.10.48.30:9092`). El loop principal está pensado para ser registrado
desde `lifespan()` en `main.py` igual que `_kafka_telemetry_loop`.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .schemas.aruba_events import parse_event, parse_weather
from .weather_db import upsert_weather_reading

log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class EventsConsumerConfig:
    enabled: bool
    bootstrap_servers: str
    security_protocol: str
    sasl_mechanism: str
    username: str
    password: str
    topic_events: str
    topic_weather: str
    group_id: str
    auto_offset_reset: str
    topics: list[str] = field(default_factory=list)


def load_events_consumer_config() -> EventsConsumerConfig:
    bootstrap = (os.environ.get("KAFKA_BOOTSTRAP_SERVERS") or "10.10.48.30:9092").strip()
    security_protocol = (os.environ.get("KAFKA_SECURITY_PROTOCOL") or "PLAINTEXT").strip()
    sasl_mechanism = (os.environ.get("KAFKA_SASL_MECHANISM") or "PLAIN").strip()
    username = (os.environ.get("KAFKA_USERNAME") or "").strip()
    password = os.environ.get("KAFKA_PASSWORD") or ""
    topic_events = (os.environ.get("KAFKA_EVENTS_TOPIC_EVENTS") or "aruba.events").strip()
    topic_weather = (os.environ.get("KAFKA_EVENTS_TOPIC_WEATHER") or "aruba.weather").strip()
    group_id = (os.environ.get("KAFKA_EVENTS_GROUP_ID") or "tres-dias-de-gracia-events").strip()
    auto_offset_reset = (os.environ.get("KAFKA_EVENTS_AUTO_OFFSET_RESET") or "earliest").strip()

    is_sasl = security_protocol.upper().startswith("SASL")
    enabled_raw = (os.environ.get("KAFKA_EVENTS_ENABLED") or "").strip()
    enabled = (
        _env_bool("KAFKA_EVENTS_ENABLED", False)
        if enabled_raw
        else bool(
            bootstrap and (topic_events or topic_weather) and ((username and password) if is_sasl else True)
        )
    )

    topics = [t for t in (topic_events, topic_weather) if t]
    return EventsConsumerConfig(
        enabled=enabled,
        bootstrap_servers=bootstrap,
        security_protocol=security_protocol,
        sasl_mechanism=sasl_mechanism,
        username=username,
        password=password,
        topic_events=topic_events,
        topic_weather=topic_weather,
        group_id=group_id,
        auto_offset_reset=auto_offset_reset,
        topics=topics,
    )


def _build_consumer_kwargs(cfg: EventsConsumerConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "bootstrap_servers": cfg.bootstrap_servers,
        "security_protocol": cfg.security_protocol,
        "group_id": cfg.group_id,
        "auto_offset_reset": cfg.auto_offset_reset,
        # Sin auto-commit: el broker nunca guarda un offset comprometido para
        # este grupo. En cada reinicio, al no haber offset previo, Kafka aplica
        # auto_offset_reset='earliest' y el consumer lee desde el principio.
        # El upsert sobre weather_readings es idempotente (ON CONFLICT id),
        # así que reprocesar mensajes ya almacenados no produce duplicados.
        "enable_auto_commit": False,
    }
    if cfg.security_protocol.upper().startswith("SASL"):
        kwargs.update(
            {
                "sasl_mechanism": cfg.sasl_mechanism,
                "sasl_plain_username": cfg.username,
                "sasl_plain_password": cfg.password,
            }
        )
    return kwargs


async def consume_aruba_events(engine: Any, *, cfg: EventsConsumerConfig) -> None:
    """Bucle de consumo (un único iter). El llamador se encarga de relanzar.

    Con `enable_auto_commit=False` el broker nunca guarda offsets para este
    grupo. En cada arranque no hay offset previo, por lo que Kafka aplica
    `auto_offset_reset='earliest'` y el consumer lee desde el principio de
    la cola. Esto garantiza que el historial completo queda en la DB.
    El upsert es idempotente (ON CONFLICT id) — reprocesar mensajes ya
    almacenados no produce filas duplicadas.
    """
    from aiokafka import AIOKafkaConsumer  # local import: dependencia opcional

    consumer = AIOKafkaConsumer(*cfg.topics, **_build_consumer_kwargs(cfg))
    await consumer.start()
    try:
        # Seek explícito al principio: ignora offsets comprometidos en el broker
        # de sesiones previas (cuando enable_auto_commit era True). Con
        # enable_auto_commit=False no se producen OffsetCommit durante el seek,
        # así que el consumer group permanece estable sin heartbeat failures.
        # Reintentos breves para esperar la asignación de particiones.
        assigned: set[Any] = set()
        for _attempt in range(15):
            assigned = consumer.assignment()
            if assigned:
                break
            await asyncio.sleep(0.2)
        if assigned:
            await consumer.seek_to_beginning(*assigned)
            log.info(
                "Aruba events consumer: seek_to_beginning en %d partición(es) "
                "— leyendo historial completo desde offset 0",
                len(assigned),
            )
        else:
            log.warning(
                "Aruba events consumer: sin particiones asignadas tras espera; "
                "usando auto_offset_reset='%s'",
                cfg.auto_offset_reset,
            )

        engine.set_aruba_events_status(
            {
                "status": "connected",
                "enabled": True,
                "topicEvents": cfg.topic_events,
                "topicWeather": cfg.topic_weather,
                "groupId": cfg.group_id,
                "lastError": None,
            }
        )
        log.info(
            "Aruba events consumer connected (%s, topics=%s, group=%s)",
            cfg.bootstrap_servers,
            cfg.topics,
            cfg.group_id,
        )
        async for msg in consumer:
            topic = str(msg.topic or "")
            value = msg.value
            if topic == cfg.topic_events:
                parsed = parse_event(value)
                if parsed is not None:
                    await engine.ingest_aruba_event(parsed.model_dump())
            elif topic == cfg.topic_weather:
                parsed = parse_weather(value)
                if parsed is not None:
                    reading = parsed.model_dump()
                    await engine.ingest_aruba_weather(reading)
                    asyncio.create_task(upsert_weather_reading(reading))
            else:
                log.debug("Aruba consumer received unexpected topic: %s", topic)
    finally:
        try:
            await consumer.stop()
        except Exception:
            pass
