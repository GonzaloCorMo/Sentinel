<script setup lang="ts">
import { storeToRefs } from "pinia";
import { ref, onMounted, onUnmounted, nextTick } from "vue";
import { useI18n } from "vue-i18n";
import { useRegionStore } from "@/stores/region";

const { t } = useI18n();
const store = useRegionStore();
const { regions, activeId, active, switching } = storeToRefs(store);

const open = ref(false);
const buttonEl = ref<HTMLButtonElement | null>(null);
const ddPos = ref<{ top: number; right: number }>({ top: 0, right: 0 });

function updatePos() {
  const el = buttonEl.value;
  if (!el) return;
  const r = el.getBoundingClientRect();
  ddPos.value = {
    top: r.bottom + 6,
    right: window.innerWidth - r.right,
  };
}

async function toggle(e: Event) {
  e.stopPropagation();
  open.value = !open.value;
  if (open.value) {
    await nextTick();
    updatePos();
  }
}

async function pick(e: Event, id: string) {
  e.stopPropagation();
  open.value = false;
  if (id === activeId.value) return;
  await store.setActive(id);
}

function onDocClick(e: MouseEvent) {
  if (!open.value) return;
  const target = e.target as Node;
  if (buttonEl.value?.contains(target)) return;
  const dd = document.getElementById("region-selector-dd");
  if (dd?.contains(target)) return;
  open.value = false;
}

function onResize() {
  if (open.value) updatePos();
}

onMounted(() => {
  document.addEventListener("click", onDocClick);
  window.addEventListener("resize", onResize);
  window.addEventListener("scroll", onResize, true);
});
onUnmounted(() => {
  document.removeEventListener("click", onDocClick);
  window.removeEventListener("resize", onResize);
  window.removeEventListener("scroll", onResize, true);
});
</script>

<template>
  <div class="relative">
    <button
      ref="buttonEl"
      type="button"
      class="flex items-center gap-1.5 rounded-lg border border-slate-700/50 bg-slate-800/50 px-2.5 py-1.5 text-xs text-slate-300 transition hover:bg-slate-700 hover:text-white disabled:cursor-wait disabled:opacity-60"
      :disabled="switching"
      :title="active?.name ?? t('region.selector_title')"
      @click="toggle"
    >
      <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 shrink-0 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
        <path stroke-linecap="round" stroke-linejoin="round" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
      <span class="max-w-[140px] truncate font-medium">{{ active?.name ?? t('region.loading') }}</span>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        class="h-3 w-3 shrink-0 text-slate-500 transition-transform"
        :class="{ 'rotate-180': open }"
        fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
      >
        <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        id="region-selector-dd"
        class="fixed w-64 overflow-hidden rounded-lg border border-slate-700/60 bg-slate-900 shadow-2xl"
        :style="{ top: ddPos.top + 'px', right: ddPos.right + 'px', zIndex: 9999 }"
      >
        <div class="border-b border-slate-800 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          {{ t('region.selector_subtitle') }}
        </div>
        <button
          v-for="r in regions"
          :key="r.id"
          type="button"
          class="flex w-full items-center justify-between gap-3 border-b border-slate-800/60 px-3 py-2.5 text-left text-xs last:border-b-0 transition"
          :class="r.id === activeId
            ? 'bg-emerald-600/15 text-emerald-300'
            : 'text-slate-200 hover:bg-slate-800 hover:text-white'"
          @click="pick($event, r.id)"
        >
          <div class="flex flex-col">
            <span class="font-semibold">{{ r.name }}</span>
            <span class="text-[10px] text-slate-500">{{ r.country }}</span>
          </div>
          <span
            v-if="r.id === activeId"
            class="rounded-full border border-emerald-400/40 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-300"
          >{{ t('region.active_badge') }}</span>
        </button>
      </div>
    </Teleport>
  </div>
</template>
