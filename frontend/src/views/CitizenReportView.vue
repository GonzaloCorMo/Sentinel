<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { getSupabase } from "@/lib/supabase";

const { t, locale } = useI18n();

type Stage = "choose" | "locating" | "listening" | "review" | "sending" | "sent" | "error";
type InputMode = "voice" | "text";

const router = useRouter();

const stage = ref<Stage>("choose");
const inputMode = ref<InputMode>("voice");
const transcript = ref("");
const interimTranscript = ref("");
const errorMsg = ref<string | null>(null);
const coords = ref<{ lat: number; lon: number; accuracy: number } | null>(null);
const emergencyType = ref<"medical" | "fire" | "police" | "accident">("medical");
const result = ref<{ id: string; title: string } | null>(null);
const userEmail = ref<string | null>(null);
const isAuthed = ref(false);

const SpeechRecognition =
  (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
const speechSupported = computed(() => !!SpeechRecognition);

// Acumulador de texto entre sesiones (continuous=false → cada start es 1 sesión).
// Al terminar una sesión lo cacheamos en accumulatedFinal; el próximo start
// empezará limpio y solo sumará lo nuevo. Esto evita las repeticiones del
// motor Android que reemite resultados anteriores.
let rec: any = null;
let accumulatedFinal = "";

onMounted(async () => {
  const sb = getSupabase();
  if (!sb) return;
  const { data } = await sb.auth.getUser();
  userEmail.value = data.user?.email ?? null;
  isAuthed.value = !!data.user;
});

function reset() {
  stage.value = "choose";
  transcript.value = "";
  interimTranscript.value = "";
  errorMsg.value = null;
  coords.value = null;
  result.value = null;
  accumulatedFinal = "";
  stopRecognition();
}

async function signOut() {
  const sb = getSupabase();
  if (!sb) return;
  await sb.auth.signOut();
  router.replace("/login");
}

async function getLocation(): Promise<void> {
  if (!navigator.geolocation) throw new Error(t("citizen.geo_unsupported"));
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        coords.value = {
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        };
        resolve();
      },
      (err) => reject(new Error(`${t("citizen.location_prefix")} ${err.message}`)),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 },
    );
  });
}

function normalize(s: string): string {
  return s.trim().replace(/\s+/g, " ");
}

function startRecognition() {
  if (!SpeechRecognition) {
    stage.value = "review";
    return;
  }
  rec = new SpeechRecognition();
  const langMap: Record<string, string> = { es: "es-ES", gl: "gl-ES", en: "en-US" };
  rec.lang = langMap[locale.value] || "es-ES";
  rec.continuous = false;    // 1 utterance por sesión, se reinicia automático en onend
  rec.interimResults = true;
  rec.maxAlternatives = 1;

  rec.onresult = (event: any) => {
    // En continuous=false, event.results contiene solo los de ESTA sesión.
    // Tomamos el último y lo mostramos; si es final lo fijamos al accumulator.
    let sessionFinal = "";
    let sessionInterim = "";
    for (let i = 0; i < event.results.length; i++) {
      const r = event.results[i];
      const text = normalize(r[0]?.transcript ?? "");
      if (r.isFinal) sessionFinal = text;
      else sessionInterim = text;
    }
    const shown = [accumulatedFinal, sessionFinal].filter(Boolean).join(" ");
    transcript.value = shown;
    interimTranscript.value = sessionInterim;
  };

  rec.onerror = (event: any) => {
    // "no-speech" es benigno — el usuario hizo pausa. Dejamos que onend
    // decida si reiniciar o no.
    if (event.error && event.error !== "no-speech" && event.error !== "aborted") {
      errorMsg.value = `${t("citizen.recognition_err")} ${event.error}`;
    }
  };

  rec.onend = () => {
    // Al cerrar una sesión, lo "final" de esa sesión ya está en transcript.value.
    // Lo consolidamos al acumulador. Si seguimos en listening, reiniciamos para
    // capturar más habla sin que Chrome re-emita lo anterior.
    accumulatedFinal = transcript.value.trim();
    interimTranscript.value = "";
    if (stage.value === "listening" && rec) {
      try {
        rec.start();
      } catch {
        /* ya terminó de verdad */
      }
    }
  };

  try { rec.start(); } catch { /* ignorar double-start */ }
}

function stopRecognition() {
  if (!rec) return;
  const r = rec;
  rec = null; // evita que onend lo reinicie
  try { r.stop(); } catch { /* ignore */ }
}

async function startVoice() {
  inputMode.value = "voice";
  transcript.value = "";
  interimTranscript.value = "";
  accumulatedFinal = "";
  errorMsg.value = null;
  stage.value = "locating";
  try {
    await getLocation();
  } catch (e) {
    errorMsg.value = (e as Error).message;
    stage.value = "error";
    return;
  }
  stage.value = "listening";
  startRecognition();
}

async function startText() {
  inputMode.value = "text";
  transcript.value = "";
  interimTranscript.value = "";
  errorMsg.value = null;
  stage.value = "locating";
  try {
    await getLocation();
  } catch (e) {
    errorMsg.value = (e as Error).message;
    stage.value = "error";
    return;
  }
  stage.value = "review";
}

function finishListening() {
  stopRecognition();
  stage.value = "review";
}

async function send() {
  if (!coords.value) {
    errorMsg.value = t("citizen.missing_location");
    stage.value = "error";
    return;
  }
  stage.value = "sending";
  errorMsg.value = null;
  const body = {
    latitude: coords.value.lat,
    longitude: coords.value.lon,
    title: buildTitle(),
    emergencyType: emergencyType.value,
    description: transcript.value || "Reporte ciudadano sin descripción",
  };
  try {
    const r = await fetch("/api/sim/emergency", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    result.value = { id: String(j.id), title: body.title };
    stage.value = "sent";
  } catch (e) {
    errorMsg.value = `${t("citizen.send_err")} ${(e as Error).message}`;
    stage.value = "error";
  }
}

function buildTitle(): string {
  const t = transcript.value.trim();
  if (!t) return `Incidencia ciudadana (${emergencyType.value})`;
  const short = t.length > 60 ? t.slice(0, 57) + "…" : t;
  return short;
}

const typeLabels: Record<string, string> = {
  medical: "Médica",
  fire: "Incendio",
  police: "Seguridad",
  accident: "Accidente",
};

onBeforeUnmount(() => { stopRecognition(); });
</script>

<template>
  <div class="cr-app">
    <header class="cr-header">
      <div class="cr-logo">
        <svg class="cr-logo-mark" viewBox="0 0 64 64" width="28" height="28" aria-hidden="true"><rect x="17" y="17" width="30" height="30" fill="none" stroke="currentColor" stroke-width="3"/><rect x="28" y="28" width="8" height="8" fill="currentColor"/></svg>
        <div class="cr-logo-text">
          <div class="cr-brand">Sentinel</div>
          <div class="cr-sub">{{ t('citizen.title') }}</div>
        </div>
      </div>
      <button v-if="isAuthed" class="cr-logout" @click="signOut" :title="t('citizen.logout')">
        <span class="cr-logout-email">{{ userEmail }}</span>
        <svg class="cr-logout-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="square" aria-hidden="true"><path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10"/></svg>
      </button>
    </header>

    <main class="cr-main">
      <!-- CHOOSE (antes idle): elige método de entrada -->
      <section v-if="stage === 'choose'" class="cr-stage">
        <h1 class="cr-title">¿Necesitas ayuda?</h1>
        <p class="cr-desc">
          Elige cómo quieres reportar la emergencia. Te localizaremos y despacharemos una unidad.
        </p>

        <div class="cr-type-picker">
          <label class="cr-type-label">{{ t('citizen.type') }}</label>
          <div class="cr-type-grid">
            <button
              v-for="(label, k) in typeLabels"
              :key="k"
              :class="['cr-type-btn', { active: emergencyType === k }]"
              @click="emergencyType = k as any"
            >
              {{ label }}
            </button>
          </div>
        </div>

        <div class="cr-mode-grid">
          <button class="cr-mode-btn voice" :disabled="!speechSupported" @click="startVoice">
            <svg class="cr-mode-icon" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>
            <div class="cr-mode-label">{{ t('citizen.by_voice') }}</div>
            <div class="cr-mode-sub">
              {{ speechSupported ? "Habla y lo transcribimos" : "No soportado en este navegador" }}
            </div>
          </button>
          <button class="cr-mode-btn text" @click="startText">
            <svg class="cr-mode-icon" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4zM13 7l4 4"/></svg>
            <div class="cr-mode-label">{{ t('citizen.by_text') }}</div>
            <div class="cr-mode-sub">{{ t('citizen.by_text_sub') }}</div>
          </button>
        </div>
      </section>

      <!-- LOCATING -->
      <section v-else-if="stage === 'locating'" class="cr-stage">
        <div class="cr-big-status">
          <div class="cr-spinner" />
          <svg class="cr-status-icon" viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M12 21s-6-5.5-6-11a6 6 0 0 1 12 0c0 5.5-6 11-6 11z"/><circle cx="12" cy="10" r="2"/></svg>
        </div>
        <h2 class="cr-title">{{ t('citizen.getting_location') }}</h2>
        <p class="cr-desc">{{ t('citizen.location_hint') }}</p>
      </section>

      <!-- LISTENING (solo modo voz) -->
      <section v-else-if="stage === 'listening'" class="cr-stage">
        <div class="cr-big-status">
          <div class="cr-pulse" />
          <svg class="cr-status-icon rec" viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>
        </div>
        <h2 class="cr-title">{{ t('citizen.listening') }}</h2>
        <p class="cr-desc">{{ t('citizen.listening_hint') }}</p>

        <div class="cr-transcript-live">
          <div v-if="transcript" class="cr-transcript-final">{{ transcript }}</div>
          <div v-if="interimTranscript" class="cr-transcript-interim">{{ interimTranscript }}</div>
          <div v-if="!transcript && !interimTranscript" class="cr-transcript-hint">
            Habla ahora...
          </div>
        </div>

        <button class="cr-btn-primary" @click="finishListening">{{ t('citizen.im_done') }}</button>
      </section>

      <!-- REVIEW -->
      <section v-else-if="stage === 'review'" class="cr-stage">
        <h2 class="cr-title">{{ t('citizen.review_title') }}</h2>

        <div class="cr-card">
          <div class="cr-card-label">{{ t('citizen.card_location') }}</div>
          <div class="cr-card-value">
            <span v-if="coords" class="cr-num">
              {{ coords.lat.toFixed(5) }}, {{ coords.lon.toFixed(5) }}
              <span class="cr-accuracy">±{{ Math.round(coords.accuracy) }} m</span>
            </span>
          </div>
          <iframe
            v-if="coords"
            class="cr-map"
            :src="`https://www.openstreetmap.org/export/embed.html?bbox=${coords.lon-0.005}%2C${coords.lat-0.003}%2C${coords.lon+0.005}%2C${coords.lat+0.003}&layer=mapnik&marker=${coords.lat}%2C${coords.lon}`"
            loading="lazy"
          />
        </div>

        <div class="cr-card">
          <div class="cr-card-label">{{ t('citizen.card_type') }}</div>
          <div class="cr-type-grid small">
            <button
              v-for="(label, k) in typeLabels"
              :key="k"
              :class="['cr-type-btn', { active: emergencyType === k }]"
              @click="emergencyType = k as any"
            >
              {{ label }}
            </button>
          </div>
        </div>

        <div class="cr-card">
          <div class="cr-card-label">
            Descripción
            <span v-if="inputMode === 'voice'" class="cr-card-hint">— desde voz, editable</span>
          </div>
          <textarea
            v-model="transcript"
            class="cr-textarea"
            :placeholder="inputMode === 'text' ? t('citizen.describe_text') : ''"
            rows="5"
          />
        </div>

        <button class="cr-btn-sos" @click="send">{{ t('citizen.submit') }}</button>
        <button class="cr-btn-ghost" @click="reset">{{ t('citizen.cancel') }}</button>
      </section>

      <!-- SENDING -->
      <section v-else-if="stage === 'sending'" class="cr-stage">
        <div class="cr-big-status">
          <div class="cr-spinner" />
        </div>
        <h2 class="cr-title">{{ t('citizen.sending') }}</h2>
      </section>

      <!-- SENT -->
      <section v-else-if="stage === 'sent'" class="cr-stage">
        <div class="cr-big-status">
          <div class="cr-check">✓</div>
        </div>
        <h2 class="cr-title">{{ t('citizen.sent_title') }}</h2>
        <p class="cr-desc">
          ID <code class="cr-num">{{ result?.id.slice(0, 8) }}</code><br/>
          Una unidad será asignada automáticamente.
        </p>
        <button class="cr-btn-primary" @click="reset">{{ t('citizen.report_another') }}</button>
      </section>

      <!-- ERROR -->
      <section v-else-if="stage === 'error'" class="cr-stage">
        <div class="cr-big-status">
          <div class="cr-cross">!</div>
        </div>
        <h2 class="cr-title">{{ t('citizen.error_title') }}</h2>
        <p class="cr-desc">{{ errorMsg }}</p>
        <button class="cr-btn-primary" @click="reset">{{ t('citizen.retry') }}</button>
      </section>
    </main>

    <footer class="cr-footer">
      <small>Demo. Para emergencias reales llama al <span class="cr-num">112</span>.</small>
    </footer>
  </div>
</template>

<style scoped>
.cr-app {
  font-family: var(--font-sans);
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  -webkit-font-smoothing: antialiased;
}

.cr-app * { box-sizing: border-box; }

.cr-num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

/* Cabecera */
.cr-header {
  height: 52px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  position: sticky; top: 0; z-index: 10;
  display: flex; justify-content: space-between; align-items: center; gap: 10px;
}
.cr-logo { display: flex; align-items: center; gap: 10px; min-width: 0; color: var(--text); }
.cr-logo-mark { flex-shrink: 0; }
.cr-logo-text { min-width: 0; line-height: 1.25; }
.cr-brand { font-size: 14px; font-weight: 600; letter-spacing: -0.01em; }
.cr-sub { font-size: 12px; color: var(--text-3); }

.cr-logout {
  display: flex; align-items: center; gap: 8px;
  height: 28px; padding: 0 10px;
  background: transparent;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  color: var(--text-2);
  font-family: inherit; font-size: 12px; cursor: pointer;
  max-width: 60%;
}
.cr-logout:hover { border-color: var(--n-600); color: var(--text); }
.cr-logout-email {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cr-logout-icon { flex-shrink: 0; }

/* Contenido */
.cr-main {
  flex: 1;
  padding: 24px 16px 96px;
  max-width: 520px;
  width: 100%;
  margin: 0 auto;
}

.cr-stage { display: flex; flex-direction: column; align-items: stretch; gap: 12px; }

.cr-title {
  font-size: 22px; font-weight: 600;
  margin: 8px 0 0; letter-spacing: -0.02em;
  text-align: center;
}
.cr-desc {
  color: var(--text-3); font-size: 14px; line-height: 1.55;
  margin: 0 0 8px; text-align: center;
}

/* Tipo de emergencia */
.cr-type-picker { margin: 12px 0 4px; }
.cr-type-label {
  font-size: 11px; font-weight: 500;
  text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--text-3);
  display: block; margin-bottom: 6px;
}
.cr-type-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px;
}
.cr-type-grid.small .cr-type-btn { height: 36px; font-size: 13px; }
.cr-type-btn {
  height: 44px;
  padding: 0 12px;
  background: transparent;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  color: var(--text-2);
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}
.cr-type-btn:hover { background: var(--surface-2); color: var(--text); }
.cr-type-btn.active {
  background: var(--n-100);
  border-color: var(--n-100);
  color: var(--n-950);
}

/* Selector de modo (voz / texto) */
.cr-mode-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
  margin-top: 16px;
}
.cr-mode-btn {
  padding: 24px 14px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-family: inherit;
  display: flex; flex-direction: column; align-items: center; gap: 6px;
}
.cr-mode-btn:hover:not(:disabled) {
  border-color: var(--n-500);
  background: var(--surface-2);
}
.cr-mode-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.cr-mode-icon { color: var(--text-2); margin-bottom: 4px; }
.cr-mode-label { font-weight: 600; font-size: 14px; }
.cr-mode-sub { font-size: 12px; color: var(--text-3); text-align: center; line-height: 1.4; }

/* Estados */
.cr-big-status {
  position: relative;
  width: 96px; height: 96px;
  margin: 24px auto 4px;
  display: flex; align-items: center; justify-content: center;
}
.cr-status-icon { position: relative; z-index: 1; color: var(--text-2); }
.cr-status-icon.rec { color: var(--crit); }

/* Grabando: anillo de alerta (indicador de micrófono activo). */
.cr-pulse {
  position: absolute; inset: 16px;
  border-radius: 50%;
  border: 1px solid var(--crit);
  animation: cr-rec 1.6s ease-out infinite;
}
@keyframes cr-rec {
  0%   { transform: scale(1); opacity: 0.9; }
  100% { transform: scale(1.5); opacity: 0; }
}

.cr-spinner {
  width: 48px; height: 48px;
  border: 2px solid var(--border-strong);
  border-top-color: var(--text);
  border-radius: 50%;
  animation: cr-spin 0.8s linear infinite;
}
.cr-big-status > .cr-spinner {
  position: absolute; inset: 12px;
  width: auto; height: auto;
}
@keyframes cr-spin { to { transform: rotate(360deg); } }

.cr-check, .cr-cross {
  width: 64px; height: 64px;
  border-radius: var(--radius-lg);
  display: flex; align-items: center; justify-content: center;
  font-family: var(--font-mono);
  font-size: 28px; font-weight: 600;
}
.cr-check {
  color: var(--ok);
  background: color-mix(in oklab, var(--ok-500) 10%, transparent);
  border: 1px solid color-mix(in oklab, var(--ok-500) 40%, transparent);
}
.cr-cross {
  color: var(--crit);
  background: color-mix(in oklab, var(--crit-500) 10%, transparent);
  border: 1px solid color-mix(in oklab, var(--crit-500) 40%, transparent);
}

/* Transcripción en vivo */
.cr-transcript-live {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 14px;
  min-height: 140px;
  font-size: 15px; line-height: 1.55;
  margin: 8px 0;
}
.cr-transcript-final { color: var(--text); }
.cr-transcript-interim { color: var(--text-3); font-style: italic; margin-top: 6px; }
.cr-transcript-hint { color: var(--text-4); text-align: center; padding: 30px 0; }

/* Tarjetas de revisión */
.cr-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
}
.cr-card-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--text-3); margin-bottom: 8px; font-weight: 500;
}
.cr-card-hint { font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--text-4); }
.cr-card-value { font-size: 13px; color: var(--text); }
.cr-accuracy { color: var(--text-4); margin-left: 6px; font-size: 12px; }
.cr-map {
  display: block;
  width: 100%; height: 180px;
  border-radius: var(--radius-sm); border: 1px solid var(--border);
  margin-top: 10px;
  filter: grayscale(1);
}
:root[data-theme="dark"] .cr-map {
  filter: grayscale(1) invert(0.92) contrast(0.9);
}
.cr-textarea {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  color: var(--text);
  font-family: inherit;
  font-size: 14px;
  padding: 10px 12px;
  resize: vertical;
  outline: none;
  transition: border-color 0.12s ease;
}
.cr-textarea::placeholder { color: var(--text-4); }
.cr-textarea:focus { border-color: var(--focus); }

/* Botones */
.cr-btn-primary,
.cr-btn-sos,
.cr-btn-ghost {
  width: 100%;
  height: 44px;
  border-radius: var(--radius-lg);
  font-family: inherit;
  font-weight: 500;
  font-size: 14px;
  cursor: pointer;
}
.cr-btn-primary {
  border: 1px solid var(--n-100);
  background: var(--n-100);
  color: var(--n-950);
}
.cr-btn-primary:hover { background: var(--n-200); border-color: var(--n-200); }

/* Envío de emergencia: único botón en color vivo. */
.cr-btn-sos {
  height: 52px;
  border: 1px solid var(--crit-600);
  background: var(--crit-600);
  color: var(--crit-50);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.01em;
}
:root[data-theme="light"] .cr-btn-sos { color: var(--crit-950); }
.cr-btn-sos:hover { background: var(--crit-500); border-color: var(--crit-500); }

.cr-btn-ghost {
  border: 1px solid var(--border-strong);
  background: transparent;
  color: var(--text-2);
}
.cr-btn-ghost:hover { background: var(--surface-2); color: var(--text); }

.cr-footer {
  padding: 14px 16px;
  text-align: center;
  color: var(--text-4);
  font-size: 11px;
  border-top: 1px solid var(--border);
}
</style>
