/** Contrato alineado con `simulation/app/engine.py` y motores en `engines/`. */

export interface PositioningTelemetry {
  latitude: number;
  longitude: number;
  speedKmh: number;
  speedMs: number;
  accelerationMs2: number;
  headingDeg: number;
  /** Límite de vía (OSRM o null) */
  roadSpeedLimitKmh?: number | null;
  gpsHdop?: number;
  gpsAccuracyM?: number;
}

export interface MechanicalTelemetry {
  fuelLevelPct: number;
  batteryPct: number;
  engineTempC: number;
  tirePressureKpa: Record<string, number>;
  tirePressurePsi?: Record<string, number>;
  oilTempC?: number;
  brakeFluidTempC?: number;
  secondaryBatteryVoltageV?: number;
  longitudinalG?: number;
  lateralG?: number;
  sirensOn: boolean;
  sirensActive?: boolean;
  odometerKm?: number;
  engineRpm?: number;
  cabinTemperatureC?: number;
}

export interface MedicalTelemetry {
  heartRateBpm: number;
  bloodPressureMmhg: { systolic: number; diastolic: number };
  spo2Pct: number;
  defibrillatorStatus: string;
  etco2MmHg?: number;
  bloodGlucoseMgDl?: number;
  bodyTempC?: number;
  infusionRateMlH?: number;
  ecgRhythm?: string;
  gcsScore?: number;
  respiratoryRatePerMin?: number;
}

export interface NetworkTelemetry {
  /** 5G / 4G / 3G según la zona; "none" en zonas sin cobertura. */
  networkType: "5G" | "4G" | "3G" | "none" | string;
  rssiDbm: number;
  latencyMs: { mqtt: number; http: number; p2p: number };
  jitterMs?: number;
  packetLossPct: number;
  throughputMbps?: number;
}

export interface AmbulanceTelemetry {
  positioning: PositioningTelemetry;
  mechanical: MechanicalTelemetry;
  /** Null cuando la unidad no transporta paciente (motor médico apagado). */
  medical: MedicalTelemetry | null;
  network?: NetworkTelemetry;
}

/** Mensaje de la central a una unidad (``unitId`` null = toda la flota). */
export interface UnitMessage {
  id: string;
  unitId: string | null;
  unitLabel?: string | null;
  text: string;
  status: "queued" | "delivered" | "read";
  createdAt: string;
  deliveredAt?: string | null;
  readAt?: string | null;
  channel?: string | null;
}

export interface EntityType {
  id: string;
  kind: "vehicle" | "place";
  name: string;
  speedKmh?: number;
  color: string;
  iconSvg?: string | null;
  description?: string | null;
  capabilities?: string | null;
  builtIn: boolean;
  // Catálogo económico/operativo (solo vehículos)
  powertrain?: "combustion" | "electric" | "unique" | null;
  crewMin?: number;
  crewMax?: number;
  costPerMin?: number;
  activationCost?: number;
}

/** Coincide con `entityTypes[].id` para tipos `place`. */
export type PoiKind = string;

export interface Poi {
  id: string;
  kind: PoiKind;
  name: string;
  latitude: number;
  longitude: number;
  source?: string;
  externalId?: string;
  address?: string | null;
  capacity?: number | null;
}

export interface Jam {
  id: string;
  polygon: [number, number][];
  source?: string;
}

/** pending → assigned (unidad en camino) → on_scene (asistiendo) → resolved. */
export type EmergencyStatus = "pending" | "assigned" | "on_scene" | "resolved";
export type EmergencyType = "medical" | "trauma" | "fire" | "hazmat" | "flood" | "altercation" | "mass_casualty";

export type EmergencySource = "training" | "manual" | "external_feed" | "citizen";

export interface Emergency {
  id: string;
  title: string;
  description?: string | null;
  latitude: number;
  longitude: number;
  status: EmergencyStatus;
  emergencyType?: EmergencyType;
  assignedAmbulanceId?: string | null;
  source?: EmergencySource | string;
  severity?: ExternalEventSeverity | string;
  /** Calle a la que se ajustó la dirección del aviso. */
  street?: string;
  /** Tipo concreto del catálogo de emergencias del motor. */
  kindKey?: string;
  createdAt?: string;
}

export type CompanionStatus = "dispatched" | "on_scene" | "returning";

export interface Companion {
  id: string;
  /** Id del tipo en `entityTypes` (p. ej. helicopter, dron creado por IA). */
  kind: string;
  /** Indicativo en mapa, p. ej. HELI-001, DRON-002. */
  displayLabel?: string;
  typeName?: string;
  latitude: number;
  longitude: number;
  assignedEmergencyId: string;
  status: CompanionStatus;
  baseLatitude: number;
  baseLongitude: number;
  speedKmh: number;
  routeCoords?: [number, number][] | null;
}

export type FsmState =
  | "IDLE"
  | "RESPONDING"
  | "REFUELING"
  | "STAGING"
  | "TRANSPORTING"
  | "UNAVAILABLE";

export interface Ambulance {
  weatherFactor?: number;
  routeDurationS?: number | null;
  id: string;
  entityTypeId?: string;
  displayLabel?: string;
  poweredOff?: boolean;
  missionStatus?: string;
  missionPhase?:
    | "idle"
    | "to_emergency"
    | "to_refuel"
    | "to_staging"
    | "to_hospital"
    | "on_scene"
    | "at_hospital"
    | "refueling";
  /** Fin de la fase con duración (en el lugar, transferencia, repostaje), en segundos simulados. */
  phaseUntil?: number | null;
  fsmState?: FsmState | string;
  speedKmh?: number;
  fuelLevel?: number;
  batteryLevel?: number;
  /** Paciente a bordo (tras recoger en emergencia hasta entregar en hospital). */
  hasPatient?: boolean;
  patientStatus?: string;
  patientSeverity?: "stable" | "moderate" | "critical";
  locationLabel?: string;
  latitude?: number;
  longitude?: number;
  baseHospitalId?: string;
  odometerKm?: number;
  updatedAt?: string;
  telemetry?: AmbulanceTelemetry;
  /** Último envío de datos recibido de la unidad (ISO). */
  lastContactAt?: string;
  routeCoords?: [number, number][] | null;
  routeProgressM?: number;
  roadSpeedLimitKmh?: number | null;
  assignedEmergencyId?: string | null;
  stagingHospitalId?: string | null;
  refuelPoiId?: string | null;
  /** Tiempo activo acumulado (sim-segundos) para cálculo de coste operativo. */
  activeSeconds?: number;
  /** True si el vehículo se ha activado al menos una vez (cobra activationCost). */
  activated?: boolean;
}

export type LinkState =
  | "mqtt_active"
  | "p2p_active"
  | "http_fallback"
  | "degraded";

export interface CommsLogEntry {
  seq: number;
  at: string;
  channel: string;
  ok: boolean;
  latencyMs: number;
  tick: number;
  error?: string | null;
  summary?: string;
}

export type MotorState = "STOPPED" | "RUNNING" | "PAUSED";

/** Estado del servicio osrm-routed (sincronizado desde la API). */
export interface OsrmRoutingStatus {
  ready: boolean;
  baseUrl: string;
  checkedAt?: string | null;
  readySince?: string | null;
  lastError?: string | null;
}

export type ExternalEventType =
  | "storm"
  | "fire"
  | "flood"
  | "accident"
  | "lane_closure"
  | "power_outage"
  | "medical_emergency"
  | "hazmat_spill"
  | "construction"
  | "public_event";

export type ExternalEventSeverity = "low" | "medium" | "high" | "critical";

export interface ExternalEvent {
  id: string;
  type: ExternalEventType | string;
  severity: ExternalEventSeverity | string;
  title: string;
  description: string;
  latitude: number;
  longitude: number;
  radius_m?: number | null;
  road_id?: string | null;
  started_at: string;
  resolved_at?: string | null;
  geometry?: number[][] | null;
  receivedAt?: string;
}

export interface WeatherReading {
  id: string;
  station_id: string;
  timestamp: string;
  temperature_c: number;
  humidity_pct: number;
  wind_speed_kmh: number;
  wind_direction_deg: number;
  pressure_hpa: number;
  precipitation_mm: number;
  visibility_km: number;
  uv_index: number;
  receivedAt?: string;
}

export interface EventSourceStatus {
  enabled?: boolean;
  status: string;
  source?: "mock" | "off" | string | null;
  lastConsumeAt?: string | null;
  consumedTotal?: number;
  consumedEvents?: number;
  consumedWeather?: number;
  lastError?: string | null;
}

export interface SimulationStatePayload {
  connected: boolean;
  updatedAt: string;
  isSimulating: boolean;
  motorState?: MotorState;
  paused?: boolean;
  /** Segundos simulados desde el arranque del motor (referencia de `phaseUntil`). */
  simTimeS?: number;
  networkStatus: { mqtt: boolean; p2p: boolean; http: boolean };
  linkState: LinkState | string;
  ambulances: Ambulance[];
  emergencies: Emergency[];
  pois: Poi[];
  jams: Jam[];
  companions?: Companion[];
  entityTypes?: EntityType[];
  dispatchRequiresApproval?: boolean;
  commsRecent?: CommsLogEntry[];
  unitMessages?: UnitMessage[];
  stats: {
    totalAmbulances: number;
    activeEmergencies: number;
    resolvedEmergencies: number;
    simulationSpeed: number;
    tickCount?: number;
  };
  lastHttpIngest?: unknown;
  osrmRouting?: OsrmRoutingStatus;
  externalEvents?: ExternalEvent[];
  weatherStations?: Record<string, WeatherReading>;
  eventSourceStatus?: EventSourceStatus;
  aiMode?: "hitl" | "autonomous";
  aiProposals?: AIProposal[];
  aiLog?: AIProposal[];
  trainingMode?: boolean;
  trainingRatePerMin?: number;
  sessionId?: string;
  externalEmergencyRatePerMin?: number;
}

export interface LLMExplanation {
  summary: string;
  keyFactors: string[];
  recommendedAction: string;
  protocolReferences?: string[];
  confidence: "low" | "medium" | "high";
  urgency: "routine" | "urgent" | "critical";
  text?: string;
}

export interface AIProposal {
  id: string;
  ambulanceId: string;
  anomalyType: string;
  anomalyDetail: Record<string, unknown>;
  matchedProtocolContent: string | null;
  llmReasoning?: string | null;
  llmExplanation?: LLMExplanation | null;
  similarity: number | null;
  status: "pending" | "approved" | "rejected" | "auto_approved";
  staleReason?: string | null;
  createdAt: string;
}

/** Herramienta de colocación en el mapa (mapeo desde builder store). */
export type MapTool = "none" | "emergency" | "hospital" | "gas" | "place" | "jam" | "ambulance" | "vehicle" | "delete";
