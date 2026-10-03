<script setup lang="ts">
import { useI18n } from "vue-i18n";

defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  close: [];
}>();

const { t } = useI18n();
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="info-modal-title"
      @keydown.escape.prevent="emit('close')"
      @click.self="emit('close')"
    >
      <div
        class="max-h-[85vh] max-w-lg overflow-y-auto rounded-2xl border border-slate-600 bg-slate-900 p-6 shadow-2xl"
        @click.stop
      >
        <h2 id="info-modal-title" class="text-lg font-semibold text-emerald-400">{{ t('info.title') }}</h2>
        <p class="mt-3 text-sm leading-relaxed text-slate-300">
          {{ t('info.intro') }}
        </p>
        <p class="mt-3 text-sm leading-relaxed text-slate-300">
          <i18n-t keypath="info.network" tag="span">
            <template #endpoint><code class="rounded bg-slate-800 px-1">/api/telemetry/ingest</code></template>
          </i18n-t>
        </p>
        <p class="mt-3 text-sm text-slate-500">
          <i18n-t keypath="info.shortcut_hint" tag="span">
            <template #key><kbd class="rounded bg-slate-800 px-2 py-0.5">i</kbd></template>
          </i18n-t>
        </p>
        <button
          type="button"
          class="mt-6 w-full rounded-lg bg-emerald-600 py-2 text-sm font-medium text-white hover:bg-emerald-500"
          @click="emit('close')"
        >
          {{ t('common.close') }}
        </button>
      </div>
    </div>
  </Teleport>
</template>
