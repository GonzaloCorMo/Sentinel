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
    <p v-if="error" class="err">{{ error }}</p>
    <p v-else>{{ t('auth_callback.verifying') }}</p>
  </div>
</template>

<style scoped>
.wrap {
  padding: 2rem;
  font-family: system-ui, sans-serif;
}
.err {
  color: #b91c1c;
}
</style>
