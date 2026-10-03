<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, computed } from "vue";
import { useI18n } from "vue-i18n";
import { setLocale, SUPPORTED_LOCALES, type Locale } from "@/i18n";

const { locale } = useI18n();

const open = ref(false);
const buttonEl = ref<HTMLButtonElement | null>(null);
const ddPos = ref<{ top: number; right: number }>({ top: 0, right: 0 });

const current = computed(() => SUPPORTED_LOCALES.find((l) => l.code === locale.value) ?? SUPPORTED_LOCALES[0]);

function updatePos() {
  const el = buttonEl.value;
  if (!el) return;
  const r = el.getBoundingClientRect();
  ddPos.value = { top: r.bottom + 6, right: window.innerWidth - r.right };
}

async function toggle(e: Event) {
  e.stopPropagation();
  open.value = !open.value;
  if (open.value) { await nextTick(); updatePos(); }
}

function pick(e: Event, code: Locale) {
  e.stopPropagation();
  open.value = false;
  if (code === locale.value) return;
  setLocale(code);
}

function onDocClick(e: MouseEvent) {
  if (!open.value) return;
  const target = e.target as Node;
  if (buttonEl.value?.contains(target)) return;
  if (document.getElementById("lang-selector-dd")?.contains(target)) return;
  open.value = false;
}

function onResize() { if (open.value) updatePos(); }

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
      class="flex items-center gap-1.5 rounded-lg border border-slate-700/50 bg-slate-800/50 px-2.5 py-1.5 text-xs font-semibold text-slate-300 transition hover:bg-slate-700 hover:text-slate-100"
      @click="toggle"
    >
      <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 5h12M9 3v2m1.048 9.5A18.022 18.022 0 016.412 9m6.088 9h7M11 21l5-10 5 10M12.751 5C11.783 10.77 8.07 15.61 3 18.129"/>
      </svg>
      <span>{{ current.short }}</span>
      <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 text-slate-500 transition-transform" :class="{ 'rotate-180': open }" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        id="lang-selector-dd"
        class="fixed w-44 overflow-hidden rounded-lg border border-slate-700/60 bg-slate-900 shadow-2xl"
        :style="{ top: ddPos.top + 'px', right: ddPos.right + 'px', zIndex: 9999 }"
      >
        <button
          v-for="l in SUPPORTED_LOCALES"
          :key="l.code"
          type="button"
          class="flex w-full items-center justify-between gap-3 border-b border-slate-800/60 px-3 py-2.5 text-left text-xs last:border-b-0 transition"
          :class="l.code === locale
            ? 'bg-emerald-600/15 text-emerald-300'
            : 'text-slate-200 hover:bg-slate-800 hover:text-slate-100'"
          @click="pick($event, l.code)"
        >
          <span class="font-semibold">{{ l.name }}</span>
          <span class="text-[10px] font-bold uppercase tracking-wider text-slate-500">{{ l.short }}</span>
        </button>
      </div>
    </Teleport>
  </div>
</template>
