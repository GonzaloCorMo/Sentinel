<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { evaluatePassword } from "@/lib/passwordStrength";
import { ROLE_LABELS, ROLES, currentRole, roleHome, type UserRole } from "@/lib/auth";

const { t } = useI18n();

type View = "login" | "register" | "forgot";

const route = useRoute();
const router = useRouter();

const view = ref<View>("login");
const loading = ref(false);
const showPassword = ref(false);
const showPasswordConfirm = ref(false);

const form = reactive({
  email: "",
  password: "",
  passwordConfirm: "",
  fullName: "",
  remember: false,
  role: "citizen" as UserRole,
});
const forgotEmail = ref("");
const errors = reactive<Record<string, string>>({});

interface Toast {
  id: number;
  type: "error" | "success" | "warning" | "info";
  title: string;
  message: string;
  icon: string;
  removing: boolean;
}
const toasts = ref<Toast[]>([]);
let toastId = 0;
const toastIcons: Record<Toast["type"], string> = {
  error: "fas fa-circle-xmark",
  success: "fas fa-circle-check",
  warning: "fas fa-triangle-exclamation",
  info: "fas fa-circle-info",
};
function addToast(type: Toast["type"], title: string, message: string) {
  const id = ++toastId;
  toasts.value.push({ id, type, title, message, icon: toastIcons[type], removing: false });
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

const particles = Array.from({ length: 20 }, (_, i) => ({
  id: i,
  x: Math.random() * 100,
  size: 2 + Math.random() * 2,
  dur: 10 + Math.random() * 15,
  del: Math.random() * 12,
}));

const configured = computed(() => isSupabaseConfigured());
const redirectTo = computed(() =>
  typeof route.query.next === "string" ? route.query.next : "/map",
);

const pwEval = computed(() => evaluatePassword(form.password));

const emailRx = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function clearError(field: string) {
  errors[field] = "";
}
function validateEmail(): boolean {
  if (!form.email) return setErr("email", t("auth.err_email_required"));
  if (!emailRx.test(form.email)) return setErr("email", t("auth.err_email_invalid"));
  errors.email = "";
  return true;
}
function validatePassword(requireStrong: boolean): boolean {
  if (!form.password) return setErr("password", t("auth.err_password_required"));
  if (requireStrong && !pwEval.value.valid) {
    return setErr("password", pwEval.value.missingReason || t("auth.err_password_weak"));
  }
  errors.password = "";
  return true;
}
function validateConfirm(): boolean {
  if (form.password !== form.passwordConfirm) {
    return setErr("passwordConfirm", t("auth.err_password_mismatch"));
  }
  errors.passwordConfirm = "";
  return true;
}
function validateForgotEmail(): boolean {
  if (!forgotEmail.value) return setErr("forgotEmail", t("auth.err_email_required"));
  if (!emailRx.test(forgotEmail.value)) return setErr("forgotEmail", t("auth.err_email_invalid"));
  errors.forgotEmail = "";
  return true;
}
function setErr(field: string, msg: string) {
  errors[field] = msg;
  return false;
}

function requireClient() {
  const sb = getSupabase();
  if (!sb) {
    addToast(
      "error",
      t("auth.supabase_not_configured"),
      t("auth.supabase_missing"),
    );
    return null;
  }
  return sb;
}

function callbackUrl(path: string): string {
  return new URL(path, window.location.origin).toString();
}

function switchView(v: View) {
  view.value = v;
  Object.keys(errors).forEach((k) => (errors[k] = ""));
}

async function handleLogin() {
  const okEmail = validateEmail();
  const okPw = validatePassword(false);
  if (!okEmail || !okPw) return;
  const sb = requireClient();
  if (!sb) return;
  loading.value = true;
  const { error } = await sb.auth.signInWithPassword({
    email: form.email,
    password: form.password,
  });
  if (error) {
    loading.value = false;
    addToast("error", t("auth.err_login"), error.message);
    return;
  }
  // Redirige al home del rol (ciudadano → /m, admin → /map, vehículo → /vehicle).
  // Respeta el `?next=` solo si el usuario tiene privilegios para esa ruta; si no,
  // el router guard lo enviará al home correcto de todos modos.
  const role = await currentRole();
  loading.value = false;
  addToast("success", t("auth.welcome"), t("auth.session_started"));
  const target = route.query.next && typeof route.query.next === "string"
    ? (route.query.next as string)
    : roleHome(role);
  await router.replace(target);
}

async function handleRegister() {
  const okEmail = validateEmail();
  const okPw = validatePassword(true);
  const okConfirm = validateConfirm();
  if (!okEmail || !okPw || !okConfirm) return;
  const sb = requireClient();
  if (!sb) return;
  loading.value = true;
  const redirect = new URL("/auth/callback", window.location.origin);
  redirect.searchParams.set("next", redirectTo.value);
  const { error } = await sb.auth.signUp({
    email: form.email,
    password: form.password,
    options: {
      emailRedirectTo: redirect.toString(),
      data: { full_name: form.fullName || null, role: form.role },
    },
  });
  loading.value = false;
  if (error) {
    addToast("error", t("auth.err_register"), error.message);
    return;
  }
  addToast("success", t("auth.account_created"), t("auth.check_email"));
  switchView("login");
}

async function handleForgot() {
  if (!validateForgotEmail()) return;
  const sb = requireClient();
  if (!sb) return;
  loading.value = true;
  const { error } = await sb.auth.resetPasswordForEmail(forgotEmail.value, {
    redirectTo: callbackUrl("/auth/update-password"),
  });
  loading.value = false;
  if (error) {
    addToast("error", t("common.error"), error.message);
    return;
  }
  addToast(
    "success",
    t("auth.link_sent"),
    t("auth.link_sent_desc"),
  );
  switchView("login");
}
</script>

<template>
  <div class="hpe-auth">
    <!-- Fondo -->
    <div class="bg-canvas">
      <div class="grid-overlay" />
      <div
        v-for="p in particles"
        :key="p.id"
        class="particle"
        :style="{
          left: p.x + '%',
          width: p.size + 'px',
          height: p.size + 'px',
          animationDuration: p.dur + 's',
          animationDelay: p.del + 's',
        }"
      />
    </div>

    <!-- Toasts -->
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
        <button class="toast-close" @click="removeToast(t.id)">
          <i class="fas fa-times" />
        </button>
      </div>
    </div>

    <div class="app-container">
      <!-- Panel izquierdo -->
      <div class="brand-panel">
        <div class="brand-logo">
          <svg viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect width="44" height="44" rx="10" fill="rgba(1,169,130,0.12)" />
            <rect x="8" y="14" width="8" height="16" rx="2" fill="#01A982" opacity="0.5" />
            <rect x="18" y="10" width="8" height="24" rx="2" fill="#01A982" />
            <rect x="28" y="16" width="8" height="12" rx="2" fill="#01A982" opacity="0.5" />
          </svg>
          <div class="brand-logo-text">HPE <span>Sentinel</span></div>
        </div>

        <h1 class="brand-tagline" v-html="t('auth.tagline_html')" />

        <p class="brand-desc">
          {{ t('auth.tagline_desc') }}
        </p>

        <div class="brand-features">
          <div class="brand-feature">
            <div class="brand-feature-icon"><i class="fas fa-shield-halved" /></div>
            <div class="brand-feature-text">
              <strong>{{ t('auth.feature_zero_trust_title') }}</strong> — {{ t('auth.feature_zero_trust_desc') }}
            </div>
          </div>
          <div class="brand-feature">
            <div class="brand-feature-icon"><i class="fas fa-network-wired" /></div>
            <div class="brand-feature-text">
              <strong>{{ t('auth.feature_telemetry_title') }}</strong> — {{ t('auth.feature_telemetry_desc') }}
            </div>
          </div>
          <div class="brand-feature">
            <div class="brand-feature-icon"><i class="fas fa-brain" /></div>
            <div class="brand-feature-text">
              <strong>{{ t('auth.feature_ai_title') }}</strong> — {{ t('auth.feature_ai_desc') }}
            </div>
          </div>
        </div>
      </div>

      <!-- Panel derecho -->
      <div class="login-panel">
        <div class="status-badge">
          <div class="status-dot" />
          {{ t('auth.all_systems_ok') }}
        </div>

        <div class="login-card">
          <div v-if="!configured" class="banner warn">
            {{ t('auth.supabase_missing') }}
          </div>

          <transition name="fade" mode="out-in">
            <!-- ===== LOGIN ===== -->
            <div v-if="view === 'login'" key="login">
              <div class="login-header">
                <h2 class="login-header-title">{{ t('auth.login_title') }}</h2>
                <p class="login-header-sub">{{ t('auth.login_subtitle') }}</p>
              </div>

              <form novalidate @submit.prevent="handleLogin">
                <div class="form-group">
                  <label class="form-label">{{ t('auth.email') }} <span class="required">*</span></label>
                  <div class="input-wrapper">
                    <input
                      v-model="form.email"
                      type="email"
                      class="form-input"
                      :class="{ error: errors.email }"
                      :placeholder="t('auth.email_placeholder')"
                      autocomplete="email"
                      @blur="validateEmail"
                      @input="clearError('email')"
                    />
                    <i class="fas fa-envelope input-icon" />
                  </div>
                  <div v-if="errors.email" class="field-error">
                    <i class="fas fa-exclamation-circle" /> {{ errors.email }}
                  </div>
                </div>

                <div class="form-group">
                  <label class="form-label">{{ t('auth.password') }} <span class="required">*</span></label>
                  <div class="input-wrapper">
                    <input
                      v-model="form.password"
                      :type="showPassword ? 'text' : 'password'"
                      class="form-input"
                      :class="{ error: errors.password }"
                      :placeholder="t('auth.password_placeholder')"
                      autocomplete="current-password"
                      style="padding-right: 46px"
                      @input="clearError('password')"
                    />
                    <i class="fas fa-lock input-icon" />
                    <button
                      type="button"
                      class="toggle-password"
                      :aria-label="showPassword ? t('auth.hide') : t('auth.show')"
                      @click="showPassword = !showPassword"
                    >
                      <i :class="showPassword ? 'fas fa-eye-slash' : 'fas fa-eye'" />
                    </button>
                  </div>
                  <div v-if="errors.password" class="field-error">
                    <i class="fas fa-exclamation-circle" /> {{ errors.password }}
                  </div>
                </div>

                <div class="form-options">
                  <label class="checkbox-wrapper">
                    <input v-model="form.remember" type="checkbox" />
                    <span class="custom-checkbox"><i class="fas fa-check" /></span>
                    <span class="checkbox-label">{{ t('auth.remember_session') }}</span>
                  </label>
                  <a href="#" class="forgot-link" @click.prevent="switchView('forgot')">
                    {{ t('auth.forgot_link') }}
                  </a>
                </div>

                <button type="submit" class="submit-btn" :disabled="loading || !configured">
                  <span class="submit-btn-content">
                    <span v-if="loading" class="btn-spinner" />
                    <i v-else class="fas fa-arrow-right-to-bracket" />
                    {{ loading ? t('auth.login_loading') : t('auth.login_btn') }}
                  </span>
                </button>
              </form>

              <div class="login-footer">
                <button type="button" class="ghost-link" @click="switchView('register')">
                  {{ t('auth.no_account') }} <strong>{{ t('auth.create_account') }}</strong>
                </button>
              </div>
            </div>

            <!-- ===== REGISTER ===== -->
            <div v-else-if="view === 'register'" key="register">
              <div class="login-header">
                <h2 class="login-header-title">{{ t('auth.register_title') }}</h2>
                <p class="login-header-sub">{{ t('auth.register_subtitle') }}</p>
              </div>

              <form novalidate @submit.prevent="handleRegister">
                <div class="form-group">
                  <label class="form-label">{{ t('auth.account_type') }} <span class="required">*</span></label>
                  <div class="role-picker">
                    <button
                      v-for="r in ROLES"
                      :key="r"
                      type="button"
                      :class="['role-btn', { active: form.role === r }]"
                      @click="form.role = r"
                    >
                      <i :class="{
                        'fas fa-user': r === 'citizen',
                        'fas fa-shield-halved': r === 'admin',
                        'fas fa-truck-medical': r === 'vehicle',
                      }" />
                      {{ ROLE_LABELS[r] }}
                    </button>
                  </div>
                </div>

                <div class="form-group">
                  <label class="form-label">{{ t('auth.full_name') }}</label>
                  <div class="input-wrapper">
                    <input
                      v-model="form.fullName"
                      type="text"
                      class="form-input"
                      :placeholder="t('auth.full_name_placeholder')"
                      autocomplete="name"
                    />
                    <i class="fas fa-user input-icon" />
                  </div>
                </div>

                <div class="form-group">
                  <label class="form-label">{{ t('auth.email') }} <span class="required">*</span></label>
                  <div class="input-wrapper">
                    <input
                      v-model="form.email"
                      type="email"
                      class="form-input"
                      :class="{ error: errors.email }"
                      :placeholder="t('auth.email_placeholder')"
                      autocomplete="email"
                      @blur="validateEmail"
                      @input="clearError('email')"
                    />
                    <i class="fas fa-envelope input-icon" />
                  </div>
                  <div v-if="errors.email" class="field-error">
                    <i class="fas fa-exclamation-circle" /> {{ errors.email }}
                  </div>
                </div>

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
                    <button
                      type="button"
                      class="toggle-password"
                      @click="showPassword = !showPassword"
                    >
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
                    <i v-else class="fas fa-user-plus" />
                    {{ loading ? t('auth.register_loading') : t('auth.register_btn') }}
                  </span>
                </button>
              </form>

              <a class="back-link" @click="switchView('login')">
                <i class="fas fa-arrow-left" /> {{ t('auth.back_to_login') }}
              </a>
            </div>

            <!-- ===== FORGOT ===== -->
            <div v-else-if="view === 'forgot'" key="forgot" class="forgot-section">
              <div class="forgot-icon-wrapper"><i class="fas fa-key" /></div>
              <h2 class="view-title">{{ t('auth.forgot_title') }}</h2>
              <p class="view-desc">
                {{ t('auth.forgot_subtitle') }}
              </p>

              <form novalidate @submit.prevent="handleForgot">
                <div class="form-group">
                  <label class="form-label">{{ t('auth.email') }} <span class="required">*</span></label>
                  <div class="input-wrapper">
                    <input
                      v-model="forgotEmail"
                      type="email"
                      class="form-input"
                      :class="{ error: errors.forgotEmail }"
                      :placeholder="t('auth.email_placeholder')"
                      autocomplete="email"
                      @blur="validateForgotEmail"
                      @input="clearError('forgotEmail')"
                    />
                    <i class="fas fa-envelope input-icon" />
                  </div>
                  <div v-if="errors.forgotEmail" class="field-error">
                    <i class="fas fa-exclamation-circle" /> {{ errors.forgotEmail }}
                  </div>
                </div>

                <button type="submit" class="submit-btn" :disabled="loading || !configured">
                  <span class="submit-btn-content">
                    <span v-if="loading" class="btn-spinner" />
                    <i v-else class="fas fa-paper-plane" />
                    {{ loading ? t('auth.sending') : t('auth.send_link') }}
                  </span>
                </button>
              </form>

              <a class="back-link" @click="switchView('login')">
                <i class="fas fa-arrow-left" /> {{ t('auth.back_to_login') }}
              </a>
            </div>
          </transition>
        </div>
      </div>
    </div>
  </div>
</template>

<style src="@/assets/auth.css"></style>
