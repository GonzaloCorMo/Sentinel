<script setup lang="ts">
/** Mini-mapa de solo lectura con un pin: confirma al ciudadano dónde le hemos localizado. */
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useTheme } from "@/composables/useTheme";
import { addBasemap, type Basemap } from "@/lib/basemap";

const props = defineProps<{ lat: number; lon: number }>();

const el = ref<HTMLDivElement | null>(null);
const { theme } = useTheme();
let map: L.Map | null = null;
let marker: L.Marker | null = null;
let basemap: Basemap | null = null;

const pin = L.divIcon({
  className: "",
  html: '<span class="location-pin" aria-hidden="true"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

onMounted(() => {
  if (!el.value) return;
  map = L.map(el.value, {
    zoomControl: false,
    attributionControl: true,
    dragging: false,
    scrollWheelZoom: false,
    doubleClickZoom: false,
    boxZoom: false,
    keyboard: false,
    touchZoom: false,
  }).setView([props.lat, props.lon], 16);
  map.attributionControl.setPrefix(false);
  basemap = addBasemap(map, theme.value);
  marker = L.marker([props.lat, props.lon], { icon: pin, interactive: false }).addTo(map);
});

watch(
  () => [props.lat, props.lon] as const,
  ([lat, lon]) => {
    map?.setView([lat, lon]);
    marker?.setLatLng([lat, lon]);
  },
);
watch(theme, (next) => basemap?.setTheme(next));

onBeforeUnmount(() => {
  basemap?.remove();
  map?.remove();
  map = null;
});
</script>

<template>
  <div ref="el" class="location-preview" role="img" :aria-label="`Ubicación ${lat.toFixed(5)}, ${lon.toFixed(5)}`" />
</template>

<style scoped>
.location-preview {
  width: 100%;
  height: 180px;
  margin-top: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.location-preview :deep(.location-pin) {
  display: block;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--crit);
  border: 3px solid #fff;
  box-shadow: 0 1px 4px rgb(0 0 0 / 0.4);
}
</style>
