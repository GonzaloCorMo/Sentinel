<script setup lang="ts">
/** Mini-mapa de solo lectura con un pin: confirma al ciudadano dónde le hemos localizado. */
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { tacticalNodeHtml } from "@/lib/tacticalMarkers";
import { createMap, maplibregl, toLngLat } from "@/lib/mapEngine";

const props = defineProps<{ lat: number; lon: number }>();

const el = ref<HTMLDivElement | null>(null);
let map: maplibregl.Map | null = null;
let marker: maplibregl.Marker | null = null;

onMounted(async () => {
  if (!el.value) return;
  map = await createMap(el.value, { center: [props.lat, props.lon], zoom: 16, interactive: false, controls: false });
  const pin = document.createElement("div");
  pin.innerHTML = tacticalNodeHtml({ shape: "diamond", tone: "crit" });
  marker = new maplibregl.Marker({ element: pin, anchor: "center" }).setLngLat(toLngLat([props.lat, props.lon])).addTo(map);
});

watch(
  () => [props.lat, props.lon] as const,
  ([lat, lon]) => {
    map?.jumpTo({ center: toLngLat([lat, lon]) });
    marker?.setLngLat(toLngLat([lat, lon]));
  },
);

onBeforeUnmount(() => {
  map?.remove();
  map = null;
});
</script>

<template>
  <div ref="el" class="location-preview sentinel-map" role="img" :aria-label="`Ubicación ${lat.toFixed(5)}, ${lon.toFixed(5)}`" />
</template>

<style scoped>
.location-preview {
  position: relative;
  width: 100%;
  height: 180px;
  margin-top: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
</style>
