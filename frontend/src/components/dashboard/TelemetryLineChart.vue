<script setup lang="ts">
/**
 * Serie temporal mínima en SVG para telemetría en vivo (sustituye a ECharts:
 * ~600 kB para dos líneas). Línea de 1.5px, rejilla recesiva, umbrales en
 * ámbar discontinuo y crosshair + tooltip al pasar el puntero.
 */
import { computed, ref } from "vue";

const props = withDefaults(
  defineProps<{
    values: number[];
    label: string;
    unit?: string;
    min?: number;
    max?: number;
    thresholds?: number[];
    height?: number;
  }>(),
  { unit: "", thresholds: () => [], height: 140 },
);

const WIDTH = 400;
const PAD = { top: 8, right: 4, bottom: 6, left: 0 };

const domain = computed(() => {
  const vals = props.values.length ? props.values : [0];
  let lo = props.min ?? Math.min(...vals, ...props.thresholds);
  let hi = props.max ?? Math.max(...vals, ...props.thresholds);
  if (hi - lo < 1) {
    lo -= 1;
    hi += 1;
  }
  return { lo, hi };
});

const innerW = WIDTH - PAD.left - PAD.right;
const innerH = computed(() => props.height - PAD.top - PAD.bottom);

function x(i: number) {
  const n = Math.max(1, props.values.length - 1);
  return PAD.left + (i / n) * innerW;
}
function y(v: number) {
  const { lo, hi } = domain.value;
  return PAD.top + (1 - (v - lo) / (hi - lo)) * innerH.value;
}

const path = computed(() =>
  props.values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
);

const ticks = computed(() => {
  const { lo, hi } = domain.value;
  return [lo, (lo + hi) / 2, hi].map((v) => ({ v, y: y(v) }));
});

const hover = ref<number | null>(null);
function onMove(e: PointerEvent) {
  const el = e.currentTarget as SVGSVGElement;
  const rect = el.getBoundingClientRect();
  const px = ((e.clientX - rect.left) / rect.width) * WIDTH;
  const n = props.values.length;
  if (!n) return;
  const i = Math.round(((px - PAD.left) / innerW) * (n - 1));
  hover.value = Math.min(n - 1, Math.max(0, i));
}
</script>

<template>
  <div class="relative pl-8">
    <span
      v-for="t in ticks"
      :key="`l-${t.v}`"
      class="absolute left-0 w-7 -translate-y-1/2 text-right font-mono text-[10px] leading-none text-slate-500"
      :style="{ top: `${(t.y / height) * 100}%` }"
    >{{ Math.round(t.v) }}</span>
    <svg
      :viewBox="`0 0 ${WIDTH} ${height}`"
      :height="height"
      class="block w-full"
      preserveAspectRatio="none"
      role="img"
      :aria-label="`${label}: ${values[values.length - 1] ?? '—'} ${unit}`"
      @pointermove="onMove"
      @pointerleave="hover = null"
    >
      <line
        v-for="t in ticks"
        :key="t.v"
        :x1="PAD.left"
        :x2="WIDTH - PAD.right"
        :y1="t.y"
        :y2="t.y"
        stroke="var(--n-800)"
        stroke-width="1"
        vector-effect="non-scaling-stroke"
      />
      <line
        v-for="th in thresholds"
        :key="`th-${th}`"
        :x1="PAD.left"
        :x2="WIDTH - PAD.right"
        :y1="y(th)"
        :y2="y(th)"
        stroke="var(--warn)"
        stroke-width="1"
        stroke-dasharray="4 3"
        vector-effect="non-scaling-stroke"
      />
      <path :d="path" fill="none" stroke="var(--n-100)" stroke-width="1.5" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
      <g v-if="hover !== null && values.length">
        <line :x1="x(hover)" :x2="x(hover)" :y1="PAD.top" :y2="height - PAD.bottom" stroke="var(--n-500)" stroke-width="1" vector-effect="non-scaling-stroke" />
      </g>
    </svg>
    <div
      v-if="hover !== null && values.length"
      class="pointer-events-none absolute top-1 rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[11px] text-slate-100"
      :style="{ left: `calc(2rem + (100% - 2rem) * ${x(hover) / WIDTH} + 6px)` }"
    >
      {{ label }} {{ values[hover] }}<span class="text-slate-500">{{ unit }}</span>
    </div>
  </div>
</template>
