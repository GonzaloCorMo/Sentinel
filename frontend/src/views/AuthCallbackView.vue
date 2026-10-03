<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { getSupabase } from "@/lib/supabase";

const { t } = useI18n();
const router = useRouter();
const error = ref<string | null>(null);

onMounted(async () => {
  const supabase = getSupabase();
  if (!supabase) {
    error.value = t("auth.supabase_missing");
    return;
  }

  // Detecta si el callback viene de un flujo de recuperación de contraseña.
  // Supabase lo indica en `type=recovery` (query o hash del enlace del email).
  const url = new URL(window.location.href);
  const qsType = url.searchParams.get("type");
  const hashParams = new URLSearchParams(url.hash.replace(/^#/, ""));
  const hashType = hashParams.get("type");
  const isRecovery = qsType === "recovery" || hashType === "recovery";

  const { error: ex } = await supabase.auth.exchangeCodeForSession(window.location.href);
  if (ex) {
    error.value = ex.message;
    return;
  }

  if (isRecovery) {
    await router.replace("/auth/update-password");
    return;
  }

  const next = url.searchParams.get("next") || "/map";
  await router.replace(next);
});
</script>

<template>
  <div class="wrap">
    <div class="box" role="status" aria-live="polite">
      <span v-if="!error" class="spinner" aria-hidden="true" />
      <p v-if="error" class="err">{{ error }}</p>
      <p v-else class="msg">{{ t('auth_callback.verifying') }}</p>
    </div>
  </div>
</template>

<style scoped>
.wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
}
.box {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  font-size: 13px;
}
.msg {
  margin: 0;
  color: var(--text-2);
}
.err {
  margin: 0;
  color: var(--crit);
}
.spinner {
  width: 12px;
  height: 12px;
  border: 1.5px solid var(--border-strong);
  border-top-color: var(--text);
  border-radius: 50%;
  animation: cb-spin 0.7s linear infinite;
}
@keyframes cb-spin {
  to { transform: rotate(360deg); }
}
</style>
