/**
 * Pinia store central del dashboard — fuente de verdad del estado del motor.
 *
 * Mantiene:
 *  - `state`: último `SimulationStatePayload` recibido por SSE o fetch.
 *  - `commsLog`: cola de mensajes del canal de comms (MQTT/P2P/HTTP).
 *  - `vitalsHistory`: series temporales cortas (MAX_POINTS) para gráficas.
 *  - Selección UI: `selectedAmbulanceId`, `selectedCompanionId`, filtros.
 *
 * Ciclo: `bootstrapSimulation()` → `fetchState()` inicial → `connectSSE()` +
 * `connectCommsSSE()` para streams. Al destruir vista: `teardownStreams()`.
 *
 * Las acciones (`spawnAmbulance`, `createEmergency`, `setAiMode`, etc.)
 * delegan en la API FastAPI y reciben el `state` actualizado en la misma
 * respuesta para evitar doble round-trip.
 */
import { defineStore } from "pinia";
import { ref, shallowRef } from "vue";
import { toast } from "vue-sonner";
import type { CommsLogEntry, MotorState, SimulationStatePayload } from "@/types/simulation";

const MAX_POINTS = 90;
const MAX_COMMS = 400;


async function parseErrorBody(r: Response): Promise<string> {
  const raw = await r.text();
  try {
    const j = JSON.parse(raw) as { detail?: unknown };
    const d = j.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d[0] && typeof (d[0] as { msg?: string }).msg === "string") {
      return (d[0] as { msg: string }).msg;
    }
  } catch {
    /* ignore */
  }
  return raw || `${r.status} ${r.statusText}`;
}

async function ensureOk(r: Response, fallback: string) {
  if (r.ok) return;
  const msg = await parseErrorBody(r);
  throw new Error(msg || fallback);
}

export const useSimulationStore = defineStore("simulation", () => {
  const state = shallowRef<SimulationStatePayload | null>(null);
  const streamStatus = ref<"idle" | "connecting" | "connected" | "reconnecting">("idle");
  const commsStreamStatus = ref("—");
  const commsLog = ref<CommsLogEntry[]>([]);
  const lastCommsSeq = ref(0);
  const selectedAmbulanceId = ref<string | null>(null);
  const selectedCompanionId = ref<string | null>(null);
  const vitalsHistory = ref<{ t: number; hr: number; spo2: number }[]>([]);
  let eventSource: EventSource | null = null;
  let commsEventSource: EventSource | null = null;
  let previousLinkState = "";
  let previousOsrmReady: boolean | null = null;
  let bootstrapped = false;

  let pendingState: SimulationStatePayload | null = null;
  let rafFlush = 0;

  /** Aplica un snapshot nuevo, emite toasts por cambios de linkState/OSRM, merge comms. */
  function applyState(next: SimulationStatePayload) {
    // Una respuesta de /api/sim/state puede llegar después de una instantánea
    // más reciente del stream: aplicarla haría retroceder las unidades.
    const cur = state.value;
    if (
      cur && next.epoch != null && next.epoch === cur.epoch &&
      (next.stats?.tickCount ?? 0) < (cur.stats?.tickCount ?? 0)
    ) {
      return;
    }
    state.value = next;
    const link = String(next.linkState);
    if (previousLinkState && link !== previousLinkState) {
      toast.info(`Canal de red: ${previousLinkState} → ${link}`, {
        description: "Conmutación MQTT → P2P → HTTP",
      });
    }
    previousLinkState = link;
    const or = next.osrmRouting;
    if (or) {
      // El estado inicial ya se ve en la cabecera: solo se avisa de transiciones.
      if (previousOsrmReady === null) {
        /* primer snapshot */
      } else if (or.ready && !previousOsrmReady) {
        toast.success("OSRM listo", {
          description: `Rutas por calle en ${or.baseUrl}`,
        });
      } else if (!or.ready && previousOsrmReady) {
        toast.warning("OSRM no disponible", {
          description: or.lastError ?? "Fallback a línea recta hasta que responda el servicio.",
        });
      }
      previousOsrmReady = or.ready;
    }
    if (next.commsRecent?.length) {
      const last = next.commsRecent[next.commsRecent.length - 1];
      if (last?.seq && last.seq > lastCommsSeq.value) {
        lastCommsSeq.value = last.seq;
      }
    }
    const cids = new Set((next.companions ?? []).map((c) => c.id));
    if (selectedCompanionId.value && !cids.has(selectedCompanionId.value)) {
      selectedCompanionId.value = null;
    }

    const ids = new Set(next.ambulances.map((a) => a.id));
    if (selectedAmbulanceId.value && !ids.has(selectedAmbulanceId.value)) {
      selectedAmbulanceId.value = null;
      vitalsHistory.value = [];
    }
    const first = next.ambulances[0];
    if (first && !selectedAmbulanceId.value && !selectedCompanionId.value) {
      selectedAmbulanceId.value = first.id;
    }
    pushVitalsFromAmbulances(next.ambulances);
  }

  function scheduleFlush() {
    if (rafFlush) return;
    rafFlush = requestAnimationFrame(() => {
      rafFlush = 0;
      if (pendingState) {
        applyState(pendingState);
        pendingState = null;
      }
    });
  }

  function pushVitalsFromAmbulances(ambulances: SimulationStatePayload["ambulances"]) {
    const id = selectedAmbulanceId.value ?? ambulances[0]?.id;
    if (!id) return;
    const amb = ambulances.find((a) => a.id === id);
    if (!amb?.hasPatient) {
      vitalsHistory.value = [];
      return;
    }
    const med = amb?.telemetry?.medical;
    if (!med) return;
    const next = [...vitalsHistory.value, { t: Date.now(), hr: med.heartRateBpm, spo2: med.spo2Pct }];
    vitalsHistory.value = next.slice(-MAX_POINTS);
  }

  /** Abre el EventSource de `/api/sim/stream`; idempotente (cierra previo). */
  function connectSSE() {
    disconnectSSE();
    streamStatus.value = "connecting";
    eventSource = new EventSource("/api/sim/stream");
    eventSource.onopen = () => {
      streamStatus.value = "connected";
    };
    eventSource.onmessage = (ev: MessageEvent) => {
      try {
        const o = JSON.parse(ev.data as string) as { state?: SimulationStatePayload };
        if (!o.state) return;
        pendingState = o.state;
        scheduleFlush();
      } catch {
        /* ignore */
      }
    };
    eventSource.onerror = () => {
      streamStatus.value = "reconnecting";
    };
  }

  function disconnectSSE() {
    eventSource?.close();
    eventSource = null;
  }

  function connectCommsSSE() {
    disconnectCommsSSE();
    const url = `/api/sim/comms/stream?since=${lastCommsSeq.value}`;
    commsEventSource = new EventSource(url);
    commsEventSource.onopen = () => {
      commsStreamStatus.value = "conectado";
    };
    commsEventSource.onmessage = (ev: MessageEvent) => {
      try {
        const row = JSON.parse(ev.data as string) as CommsLogEntry;
        if (row.seq != null) {
          lastCommsSeq.value = Math.max(lastCommsSeq.value, row.seq);
        }
        commsLog.value = [...commsLog.value.slice(-(MAX_COMMS - 1)), row];
      } catch {
        /* ignore */
      }
    };
    commsEventSource.onerror = () => {
      commsStreamStatus.value = "error / reconectando…";
    };
  }

  function disconnectCommsSSE() {
    commsEventSource?.close();
    commsEventSource = null;
  }

  async function fetchState() {
    const r = await fetch("/api/sim/state", { cache: "no-store" });
    await ensureOk(r, "No se pudo leer el estado de simulación");
    const j = (await r.json()) as SimulationStatePayload;
    applyState(j);
    if (j.commsRecent?.length) {
      commsLog.value = j.commsRecent.slice(-MAX_COMMS);
      const last = j.commsRecent[j.commsRecent.length - 1];
      if (last?.seq != null) {
        lastCommsSeq.value = last.seq;
      }
    }
  }

  /** Orquesta el arranque: fetch inicial + apertura de ambos streams SSE. */
  async function bootstrapSimulation() {
    await fetchState();
    if (!bootstrapped) {
      bootstrapped = true;
      connectSSE();
      connectCommsSSE();
    }
  }

  function teardownStreams() {
    disconnectSSE();
    disconnectCommsSSE();
    bootstrapped = false;
  }

  /** Play / Pause / Reset + speedMultiplier al motor (POST /api/sim/control). */
  async function postControl(body: { action?: "play" | "pause" | "reset"; speedMultiplier?: number }) {
    const r = await fetch("/api/sim/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await ensureOk(r, "Error en control de simulación");
    await fetchState();
  }

  /** Toggle canales de comms (MQTT / P2P / HTTP). El motor degrada al
   *  siguiente canal disponible y mantiene telemetría/comandos. */
  async function setNetwork(net: { mqtt: boolean; p2p: boolean; http: boolean }) {
    const r = await fetch("/api/sim/network", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(net),
    });
    await ensureOk(r, "Error cambiando canales de comms");
    await fetchState();
  }

  /** Mensaje de la central a una unidad (``null`` = toda la flota). */
  async function sendUnitMessage(unitId: string | null, text: string) {
    const r = await fetch("/api/comms/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unitId, text }),
    });
    await ensureOk(r, "Error enviando el mensaje");
    await fetchState();
  }

  /** Añade una unidad nueva. Si `entityTypeId` no se pasa, el backend usa `ambulance`. */
  async function spawnAmbulance(
    latitude: number,
    longitude: number,
    entityTypeId?: string,
    displayLabel?: string,
  ) {
    const body: Record<string, unknown> = { latitude, longitude };
    if (entityTypeId) body.entityTypeId = entityTypeId;
    if (displayLabel) body.displayLabel = displayLabel;
    const r = await fetch("/api/sim/spawn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await ensureOk(r, "No se pudo añadir la unidad");
    await fetchState();
  }

  /** Crea una emergencia manualmente. La PWA ciudadana usa el mismo endpoint. */
  async function createEmergency(latitude: number, longitude: number, title?: string, emergencyType?: string, description?: string) {
    const r = await fetch("/api/sim/emergency", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ latitude, longitude, title, emergencyType: emergencyType ?? "medical", description: description || null }),
    });
    await ensureOk(r, "No se pudo registrar la emergencia");
    await fetchState();
  }

  async function createJamAtPoint(latitude: number, longitude: number, radiusM = 70) {
    const r = await fetch("/api/sim/jam/point", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ latitude, longitude, radiusM }),
    });
    await ensureOk(r, "No se pudo crear la zona de atasco");
    await fetchState();
  }

  async function createPoi(kind: string, latitude: number, longitude: number, name?: string) {
    const r = await fetch("/api/sim/poi", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind,
        name: name ?? "POI",
        latitude,
        longitude,
      }),
    });
    await ensureOk(r, "No se pudo añadir el POI");
    await fetchState();
  }

  /** Elimina un objeto del mapa según tipo. Wrapper común para el modo borrar. */
  async function deleteMapObject(
    kind: "ambulance" | "companion" | "emergency" | "poi" | "jam",
    id: string,
  ) {
    const r = await fetch(`/api/sim/${kind}/${id}`, { method: "DELETE" });
    await ensureOk(r, `No se pudo eliminar ${kind}`);
    await fetchState();
  }

  /** Aprueba o rechaza una propuesta HITL desde el panel lateral. */
  async function resolveProposal(proposalId: string, action: "approved" | "rejected") {
    const r = await fetch(`/api/ai/proposals/${proposalId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    await ensureOk(r, "No se pudo resolver la propuesta IA");
    await fetchState();
  }

  /** Activa/desactiva modo simulación autónoma para alimentar pipeline ML. */
  async function setTrainingMode(enabled: boolean, ratePerMin?: number) {
    const r = await fetch("/api/sim/training-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, ratePerMin }),
    });
    await ensureOk(r, "No se pudo cambiar el modo de entrenamiento");
    await fetchState();
  }

  /** Cambia el modo de la IA (HITL ↔ autónomo); el backend auto-aprueba pendientes al pasar a auto. */
  async function setAiMode(mode: "hitl" | "autonomous") {
    const r = await fetch("/api/ai/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    await ensureOk(r, "No se pudo cambiar el modo IA");
    await fetchState();
  }

  function selectAmbulance(id: string | null) {
    selectedCompanionId.value = null;
    selectedAmbulanceId.value = id;
    vitalsHistory.value = [];
  }

  function selectCompanion(id: string | null) {
    selectedAmbulanceId.value = null;
    selectedCompanionId.value = id;
    vitalsHistory.value = [];
  }

  // ── Filtros UI globales aplicados por el chatbot de comandos ─────────
  const uiFilters = ref<{
    fuelBelow?: number;
    fuelAbove?: number;
    batteryBelow?: number;
    hasPatient?: boolean;
    severity?: "stable" | "moderate" | "critical";
    entityTypeId?: string;
    missionPhase?: string;
    poweredOff?: boolean;
    sortBy?: "fuel" | "battery" | "severity" | "distance";
    limit?: number;
  }>({});

  function setUiFilters(f: Partial<typeof uiFilters.value>) {
    uiFilters.value = { ...uiFilters.value, ...f };
  }
  function clearUiFilters() {
    uiFilters.value = {};
  }

  return {
    state,
    streamStatus,
    commsLog,
    lastCommsSeq,
    selectedAmbulanceId,
    selectedCompanionId,
    vitalsHistory,
    connectSSE,
    disconnectSSE,
    connectCommsSSE,
    disconnectCommsSSE,
    fetchState,
    bootstrapSimulation,
    teardownStreams,
    postControl,
    setNetwork,
    sendUnitMessage,
    spawnAmbulance,
    uiFilters,
    setUiFilters,
    clearUiFilters,
    createEmergency,
    createJamAtPoint,
    createPoi,
    deleteMapObject,
    resolveProposal,
    setAiMode,
    setTrainingMode,
    selectAmbulance,
    selectCompanion,
  };
});
