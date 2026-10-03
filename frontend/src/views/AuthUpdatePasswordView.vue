<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { evaluatePassword } from "@/lib/passwordStrength";

const { t } = useI18n();

const router = useRouter();
const loading = ref(false);
const showPassword = ref(false);
const showPasswordConfirm = ref(false);
const done = ref(false);

const form = reactive({ password: "", passwordConfirm: "" });
const errors = reactive<Record<string, string>>({});

interface Toast {
  id: number;
  type: "error" | "success";
  title: string;
  message: string;
  icon: string;
  removing: boolean;
}
const toasts = ref<Toast[]>([]);
let toastId = 0;
function addToast(type: Toast["type"], title: string, message: string) {
  const id = ++toastId;
  toasts.value.push({
    id,
    type,
    title,
    message,
    icon: type === "error" ? "fas fa-circle-xmark" : "fas fa-circle-check",
    removing: false,
  });
  setTimeout(() => removeToast(id), 4500);
}
function removeToast(id: number) {
  const t = toasts.value.find((x) => x.id === id);
  if (!t) return;
  t.removing = true;
  setTimeout(() => {
    const i = toasts.value.findIndex((x) => x.id === id);
    if (i > -1) toasts.value.splice(i, 1);
  }, 300);
}

const configured = computed(() => isSupabaseConfigured());
const pwEval = computed(() => evaluatePassword(form.password));

function clearError(field: string) { errors[field] = ""; }

async function handleUpdate() {
  errors.password = "";
  errors.passwordConfirm = "";
  if (!form.password) { errors.password = t("auth.err_password_required"); return; }
  if (!pwEval.value.valid) { errors.password = pwEval.value.missingReason || t("auth.err_password_weak"); return; }
  if (form.password !== form.passwordConfirm) { errors.passwordConfirm = t("auth.err_password_mismatch"); return; }
  const sb = getSupabase();
  if (!sb) {
    addToast("error", t("auth.supabase_not_configured"), t("auth.supabase_missing"));
    return;
  }
  loading.value = true;
  const { error } = await sb.auth.updateUser({ password: form.password });
  loading.value = false;
  if (error) {
    addToast("error", t("common.error"), error.message);
    return;
  }
  done.value = true;
  addToast("success", t("auth.password_updated"), t("auth.password_updated_desc"));
  setTimeout(() => router.replace("/map"), 2500);
}
</script>

<template>
  <div class="auth">

    <div class="toast-container">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="toast"
        :class="[t.type, { removing: t.removing }]"
      >
        <div class="toast-icon"><i :class="t.icon" /></div>
        <div class="toast-content">
          <div class="toast-title">{{ t.title }}</div>
          <div class="toast-message">{{ t.message }}</div>
        </div>
        <button class="toast-close" @click="removeToast(t.id)"><i class="fas fa-times" /></button>
      </div>
    </div>

    <div class="app-container">
      <div class="login-panel center">
        <div class="login-card">
          <div v-if="!configured" class="banner warn">
            {{ t('auth.supabase_missing') }}
          </div>

          <div v-if="done" class="forgot-section">
            <div class="success-icon-wrapper"><i class="fas fa-check" /></div>
            <h2 class="view-title">{{ t('auth.password_updated') }}</h2>
            <p class="view-desc">{{ t('auth_callback.redirecting') }}</p>
          </div>

          <div v-else>
            <div class="forgot-icon-wrapper"><i class="fas fa-key" /></div>
            <h2 class="view-title">{{ t('auth.update_password_title') }}</h2>
            <p class="view-desc">
              {{ t('auth.update_password_subtitle') }}
            </p>

            <form novalidate @submit.prevent="handleUpdate">
              <div class="form-group">
                <label class="form-label">{{ t('auth.password') }} <span class="required">*</span></label>
                <div class="input-wrapper">
                  <input
                    v-model="form.password"
                    :type="showPassword ? 'text' : 'password'"
                    class="form-input"
                    :class="{ error: errors.password }"
                    :placeholder="t('auth.password_strong_placeholder')"
                    autocomplete="new-password"
                    style="padding-right: 46px"
                    @input="clearError('password')"
                  />
                  <i class="fas fa-lock input-icon" />
                  <button type="button" class="toggle-password" @click="showPassword = !showPassword">
                    <i :class="showPassword ? 'fas fa-eye-slash' : 'fas fa-eye'" />
                  </button>
                </div>
                <div v-if="errors.password" class="field-error">
                  <i class="fas fa-exclamation-circle" /> {{ errors.password }}
                </div>
                <div v-if="form.password.length > 0" class="password-strength">
                  <div
                    v-for="i in 4"
                    :key="i"
                    class="strength-bar"
                    :class="{ active: pwEval.score >= i, [pwEval.level]: true }"
                  />
                </div>
                <div v-if="form.password.length > 0" class="strength-text" :class="pwEval.level">
                  {{ pwEval.label }}
                </div>
                <ul v-if="form.password.length > 0" class="pw-requirements">
                  <li :class="{ met: pwEval.requirements.length }">
                    <i :class="pwEval.requirements.length ? 'fas fa-check' : 'fas fa-xmark'" />
                    {{ t('auth.pw_min_chars') }}
                  </li>
                  <li :class="{ met: pwEval.requirements.upper }">
                    <i :class="pwEval.requirements.upper ? 'fas fa-check' : 'fas fa-xmark'" />
                    {{ t('auth.pw_uppercase') }}
                  </li>
                  <li :class="{ met: pwEval.requirements.lower }">
                    <i :class="pwEval.requirements.lower ? 'fas fa-check' : 'fas fa-xmark'" />
                    {{ t('auth.pw_lowercase') }}
                  </li>
                  <li :class="{ met: pwEval.requirements.digit }">
                    <i :class="pwEval.requirements.digit ? 'fas fa-check' : 'fas fa-xmark'" />
                    {{ t('auth.pw_digit') }}
                  </li>
                </ul>
              </div>

              <div class="form-group">
                <label class="form-label">{{ t('auth.password_confirm') }} <span class="required">*</span></label>
                <div class="input-wrapper">
                  <input
                    v-model="form.passwordConfirm"
                    :type="showPasswordConfirm ? 'text' : 'password'"
                    class="form-input"
                    :class="{ error: errors.passwordConfirm }"
                    :placeholder="t('auth.password_confirm_placeholder')"
                    autocomplete="new-password"
                    style="padding-right: 46px"
                    @input="clearError('passwordConfirm')"
                  />
                  <i class="fas fa-lock input-icon" />
                  <button
                    type="button"
                    class="toggle-password"
                    @click="showPasswordConfirm = !showPasswordConfirm"
                  >
                    <i :class="showPasswordConfirm ? 'fas fa-eye-slash' : 'fas fa-eye'" />
                  </button>
                </div>
                <div v-if="errors.passwordConfirm" class="field-error">
                  <i class="fas fa-exclamation-circle" /> {{ errors.passwordConfirm }}
                </div>
              </div>

              <button type="submit" class="submit-btn" :disabled="loading || !configured">
                <span class="submit-btn-content">
                  <span v-if="loading" class="btn-spinner" />
                  <i v-else class="fas fa-check" />
                  {{ loading ? t('auth.sending') : t('auth.update_password_btn') }}
                </span>
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style src="@/assets/auth.css"></style>
