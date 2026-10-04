/**
 * Store del modo constructor de escenarios (``/map``).
 *
 * Mantiene qué herramienta está activa (`currentBuilderTool`) para que el
 * click en el mapa cree el elemento correcto: POI, vehículo, emergencia o
 * atasco. También memoriza el `entityTypeId` cuando la herramienta genérica
 * (`add_place` / `add_vehicle`) necesita saber qué tipo concreto colocar.
 */
import { defineStore } from "pinia";
import { ref } from "vue";

/** Herramientas del constructor de escenario (estado cero). */
export type BuilderTool =
  | null
  | "add_hospital"
  | "add_gas_station"
  | "add_place"
  | "add_ambulance"
  | "add_vehicle"
  | "add_emergency"
  | "add_traffic"
  | "delete";

export const useBuilderStore = defineStore("builder", () => {
  const currentBuilderTool = ref<BuilderTool>(null);
  const placeEntityId = ref<string | null>(null);
  /** Para `add_vehicle`: id del tipo del catálogo a colocar (si no es `ambulance`). */
  const vehicleEntityId = ref<string | null>(null);

  function resetAuxIds(t: BuilderTool) {
    if (t !== "add_place") placeEntityId.value = null;
    if (t !== "add_vehicle") vehicleEntityId.value = null;
  }

  /** Fija herramienta y limpia ids auxiliares no aplicables. */
  function setTool(t: BuilderTool) {
    currentBuilderTool.value = t;
    resetAuxIds(t);
  }

  function clearTool() {
    currentBuilderTool.value = null;
    placeEntityId.value = null;
    vehicleEntityId.value = null;
  }

  /** Activa colocación de place; ``hospital``/``gas_station`` tienen herramienta propia. */
  function selectPlaceEntity(entityId: string) {
    if (entityId === "hospital") {
      placeEntityId.value = null;
      currentBuilderTool.value = "add_hospital";
      vehicleEntityId.value = null;
      return;
    }
    if (entityId === "gas_station") {
      placeEntityId.value = null;
      currentBuilderTool.value = "add_gas_station";
      vehicleEntityId.value = null;
      return;
    }
    placeEntityId.value = entityId;
    currentBuilderTool.value = "add_place";
    vehicleEntityId.value = null;
  }

  /** Activa colocación de un tipo de vehículo del catálogo. */
  function selectVehicleEntity(entityId: string) {
    if (entityId === "ambulance") {
      vehicleEntityId.value = null;
      currentBuilderTool.value = "add_ambulance";
      placeEntityId.value = null;
      return;
    }
    vehicleEntityId.value = entityId;
    currentBuilderTool.value = "add_vehicle";
    placeEntityId.value = null;
  }

  return {
    currentBuilderTool,
    placeEntityId,
    vehicleEntityId,
    setTool,
    clearTool,
    selectPlaceEntity,
    selectVehicleEntity,
  };
});
