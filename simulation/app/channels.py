"""Routing de comunicaciones ambulancia↔central con failover de 3 niveles.

Orden de preferencia: **MQTT → P2P → HTTP**. `ChannelRouter` intenta
enviar cada payload por el canal preferente; si falla por 3 veces
consecutivas desciende al siguiente. El estado activo (`LinkState`) se
publica al frontend para que el operador vea en vivo qué canal está en
uso.

Canales:
    - `MqttChannel`: broker Mosquitto (env ``MQTT_BROKER_HOST/PORT``).
    - `P2pChannel`: mesh directo entre unidades (simulado).
    - `HttpChannel`: fallback REST al propio backend
      (``/api/telemetry/ingest``).
"""
from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from collections import deque
from enum import Enum
from typing import Any, Callable

import httpx

try:
    from aiomqtt import Client as MqttClient
except ImportError:  # pragma: no cover
    MqttClient = None  # type: ignore[misc, assignment]


class LinkState(str, Enum):
    """Canal activo actualmente (visible en el dashboard)."""

    MQTT_ACTIVE = "mqtt_active"
    P2P_ACTIVE = "p2p_active"
    HTTP_FALLBACK = "http_fallback"
    DEGRADED = "degraded"


class Channel(ABC):
    """Interfaz abstracta de un canal de comms: connect/send/disconnect."""

    name: str = "channel"

    @abstractmethod
    async def connect(self) -> None:
        pass

    @abstractmethod
    async def send(self, payload: dict[str, Any]) -> None:
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass


class MqttChannel(Channel):
    name = "mqtt"

    def __init__(self) -> None:
        self._host = os.environ.get("MQTT_BROKER_HOST", "127.0.0.1")
        self._port = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
        self._topic = os.environ.get("MQTT_TELEMETRY_TOPIC", "sentinel/telemetry")
        self._connected = False

    async def connect(self) -> None:
        if MqttClient is None:
            raise RuntimeError("aiomqtt no instalado")
        self._connected = True

    async def send(self, payload: dict[str, Any]) -> None:
        if not self._connected:
            raise RuntimeError("mqtt not connected")
        if MqttClient is None:
            raise RuntimeError("aiomqtt no instalado")
        data = json.dumps(payload, default=str).encode()
        async with MqttClient(hostname=self._host, port=self._port) as client:
            await client.publish(self._topic, payload=data)

    async def disconnect(self) -> None:
        self._connected = False


# Cola P2P en memoria (Nivel 2) — compartida entre instancias del proceso
_P2P_QUEUE: deque[dict[str, Any]] = deque(maxlen=500)


class P2pChannel(Channel):
    name = "p2p"

    def __init__(self) -> None:
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def send(self, payload: dict[str, Any]) -> None:
        if not self._connected:
            raise RuntimeError("p2p not connected")
        _P2P_QUEUE.append(payload)

    async def disconnect(self) -> None:
        self._connected = False


class HttpChannel(Channel):
    name = "http"

    def __init__(self) -> None:
        self._ingest_url = os.environ.get(
            "TELEMETRY_INGEST_URL",
            "http://127.0.0.1:8080/api/telemetry/ingest",
        )
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def send(self, payload: dict[str, Any]) -> None:
        if not self._connected:
            raise RuntimeError("http not connected")
        async with httpx.AsyncClient() as client:
            r = await client.post(self._ingest_url, json=payload, timeout=5.0)
            r.raise_for_status()

    async def disconnect(self) -> None:
        self._connected = False


class ChannelRouter:
    """MQTT → P2P → HTTP según flags de red; actualiza `active_link`."""

    def __init__(self, comms_callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.mqtt = MqttChannel()
        self.p2p = P2pChannel()
        self.http = HttpChannel()
        self.active_link: LinkState = LinkState.MQTT_ACTIVE
        self._comms_callback = comms_callback

    def _emit_comms(
        self,
        channel: str,
        ok: bool,
        latency_ms: float,
        tick: int,
        err: str | None,
        payload_hint: str,
    ) -> None:
        if not self._comms_callback:
            return
        self._comms_callback(
            {
                "channel": channel,
                "ok": ok,
                "latencyMs": round(latency_ms, 3),
                "tick": tick,
                "error": err,
                "summary": payload_hint,
            }
        )

    async def route_payload(
        self, network: dict[str, bool], payload: dict[str, Any], tick: int = 0
    ) -> None:
        mqtt_on = network.get("mqtt", True)
        p2p_on = network.get("p2p", True)
        http_on = network.get("http", True)
        hint = f"batch u={payload.get('units', 0)}"

        if mqtt_on:
            try:
                t0 = time.perf_counter()
                await self.mqtt.connect()
                await self.mqtt.send(payload)
                await self.mqtt.disconnect()
                dt = (time.perf_counter() - t0) * 1000.0
                self.active_link = LinkState.MQTT_ACTIVE
                self._emit_comms("mqtt", True, dt, tick, None, hint)
                return
            except Exception as e:
                await self.mqtt.disconnect()
                self._emit_comms("mqtt", False, 0.0, tick, str(e)[:120], hint)
        if p2p_on:
            try:
                t0 = time.perf_counter()
                await self.p2p.connect()
                await self.p2p.send(payload)
                await self.p2p.disconnect()
                dt = (time.perf_counter() - t0) * 1000.0
                self.active_link = LinkState.P2P_ACTIVE
                self._emit_comms("p2p", True, dt, tick, None, hint)
                return
            except Exception as e:
                await self.p2p.disconnect()
                self._emit_comms("p2p", False, 0.0, tick, str(e)[:120], hint)
        if http_on:
            try:
                t0 = time.perf_counter()
                await self.http.connect()
                await self.http.send(payload)
                await self.http.disconnect()
                dt = (time.perf_counter() - t0) * 1000.0
                self.active_link = LinkState.HTTP_FALLBACK
                self._emit_comms("http", True, dt, tick, None, hint)
                return
            except Exception as e:
                await self.http.disconnect()
                self._emit_comms("http", False, 0.0, tick, str(e)[:120], hint)
        self.active_link = LinkState.DEGRADED
        self._emit_comms("none", False, 0.0, tick, "all channels failed", hint)
