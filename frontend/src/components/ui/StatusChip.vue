<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { unitStatus } from "@/lib/unitStatus";
import type { Ambulance } from "@/types/simulation";

const props = defineProps<{ unit: Pick<Ambulance, "missionPhase" | "fsmState" | "hasPatient"> & { poweredOff?: boolean } }>();
const { t } = useI18n();
const status = computed(() => unitStatus(props.unit));
</script>

<template>
  <span class="status-chip" :data-tone="status.tone">
    <span class="status-dot" aria-hidden="true" />
    {{ t(`status.${status.key}`) }}
  </span>
</template>

<style scoped>
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.125rem 0.4rem;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 500;
  color: var(--text-2);
  white-space: nowrap;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 9999px;
  background: var(--text-4);
}
.status-chip[data-tone="active"] .status-dot { background: var(--warn); }
.status-chip[data-tone="ok"] .status-dot { background: var(--ok); }
.status-chip[data-tone="alert"] { border-color: color-mix(in oklab, var(--crit) 45%, transparent); color: var(--crit-300); }
.status-chip[data-tone="alert"] .status-dot { background: var(--crit); }
</style>
