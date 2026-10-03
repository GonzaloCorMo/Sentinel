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
        <div class="cr-logo-icon">
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 2L4 5v6c0 5 3.4 9.5 8 11 4.6-1.5 8-6 8-11V5l-8-3z" fill="#01A982"/>
            <path d="M12 8v8M8 12h8" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/>
          </svg>
        </div>
        <div class="cr-logo-text">
          <div class="cr-brand">HPE Sentinel</div>
          <div class="cr-sub">{{ t('citizen.title') }}</div>
        </div>
      </div>
      <button v-if="isAuthed" class="cr-logout" @click="signOut" :title="t('citizen.logout')">
        <span class="cr-logout-email">{{ userEmail }}</span>
        <span class="cr-logout-icon">⎋</span>
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
            <div class="cr-mode-icon">🎙️</div>
            <div class="cr-mode-label">{{ t('citizen.by_voice') }}</div>
            <div class="cr-mode-sub">
              {{ speechSupported ? "Habla y lo transcribimos" : "No soportado en este navegador" }}
            </div>
          </button>
          <button class="cr-mode-btn text" @click="startText">
            <div class="cr-mode-icon">✏️</div>
            <div class="cr-mode-label">{{ t('citizen.by_text') }}</div>
            <div class="cr-mode-sub">{{ t('citizen.by_text_sub') }}</div>
          </button>
        </div>
      </section>

      <!-- LOCATING -->
      <section v-else-if="stage === 'locating'" class="cr-stage">
        <div class="cr-big-status">
          <div class="cr-pulse cr-pulse-blue" />
          <div class="cr-status-icon">📍</div>
        </div>
        <h2 class="cr-title">{{ t('citizen.getting_location') }}</h2>
        <p class="cr-desc">{{ t('citizen.location_hint') }}</p>
      </section>

      <!-- LISTENING (solo modo voz) -->
      <section v-else-if="stage === 'listening'" class="cr-stage">
        <div class="cr-big-status">
          <div class="cr-pulse cr-pulse-red" />
          <div class="cr-status-icon">🎙️</div>
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
            <span v-if="coords">
              {{ coords.lat.toFixed(5) }}, {{ coords.lon.toFixed(5) }}
              <span class="cr-accuracy">±{{ Math.round(coords.accuracy) }}m</span>
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

        <button class="cr-btn-primary big" @click="send">{{ t('citizen.submit') }}</button>
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
          ID: <code>{{ result?.id.slice(0, 8) }}</code><br/>
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
      <small>HPE CDS Tech Challenge · Demo. Para emergencias reales llama al 112.</small>
    </footer>
  </div>
</template>

<style scoped>
@import url("https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap");

.cr-app {
  --green: #01a982;
  --green-glow: rgba(1, 169, 130, 0.25);
  --navy: #0f1b2d;
  --navy-mid: #162036;
  --navy-light: #1e2d4a;
  --red: #e53e3e;
  --red-glow: rgba(229, 62, 62, 0.4);
  --blue: #3b82f6;
  --white: #fff;
  --muted: rgba(255, 255, 255, 0.55);
  --dim: rgba(255, 255, 255, 0.3);

  font-family: "IBM Plex Sans", sans-serif;
  background: var(--navy);
  color: var(--white);
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  -webkit-font-smoothing: antialiased;
}

.cr-app * { box-sizing: border-box; }

.cr-header {
  padding: 14px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  background: rgba(255, 255, 255, 0.02);
  backdrop-filter: blur(10px);
  position: sticky; top: 0; z-index: 10;
  display: flex; justify-content: space-between; align-items: center; gap: 10px;
}
.cr-logo { display: flex; align-items: center; gap: 12px; min-width: 0; }
.cr-logo-icon {
  width: 40px; height: 40px; flex-shrink: 0;
  background: rgba(1, 169, 130, 0.12);
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
}
.cr-logo-icon svg { width: 26px; height: 26px; }
.cr-logo-text { min-width: 0; }
.cr-brand { font-family: "Space Grotesk"; font-size: 16px; font-weight: 700; }
.cr-sub { font-size: 12px; color: var(--muted); }

.cr-logout {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  color: var(--muted);
  font-family: inherit; font-size: 12px; cursor: pointer;
  transition: 0.2s;
  max-width: 60%;
}
.cr-logout:hover { border-color: var(--red); color: var(--red); }
.cr-logout-email {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cr-logout-icon { font-size: 16px; line-height: 1; flex-shrink: 0; }

.cr-main {
  flex: 1;
  padding: 24px 20px 100px;
  max-width: 520px;
  width: 100%;
  margin: 0 auto;
}

.cr-stage { display: flex; flex-direction: column; align-items: stretch; gap: 16px; }

.cr-title {
  font-family: "Space Grotesk";
  font-size: 26px; font-weight: 700;
  margin-top: 8px; letter-spacing: -0.3px;
  text-align: center;
}
.cr-desc {
  color: var(--muted); font-size: 14px; line-height: 1.55;
  margin-bottom: 8px; text-align: center;
}

.cr-type-picker { margin: 12px 0 4px; }
.cr-type-label {
  font-size: 12px; color: var(--muted);
  display: block; margin-bottom: 8px;
}
.cr-type-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px;
}
.cr-type-grid.small .cr-type-btn { padding: 10px 8px; font-size: 13px; }
.cr-type-btn {
  padding: 14px 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  color: var(--white);
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: 0.2s;
}
.cr-type-btn.active {
  background: rgba(1, 169, 130, 0.12);
  border-color: var(--green);
  color: var(--green);
}

/* Mode picker (voice/text) */
.cr-mode-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
  margin-top: 20px;
}
.cr-mode-btn {
  padding: 28px 16px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.03);
  color: var(--white);
  cursor: pointer;
  font-family: inherit;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  transition: 0.25s;
}
.cr-mode-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  border-color: var(--green);
  background: rgba(1, 169, 130, 0.06);
  box-shadow: 0 8px 24px rgba(1, 169, 130, 0.15);
}
.cr-mode-btn.text:hover:not(:disabled) {
  border-color: #3b82f6;
  background: rgba(59, 130, 246, 0.08);
  box-shadow: 0 8px 24px rgba(59, 130, 246, 0.15);
}
.cr-mode-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.cr-mode-icon { font-size: 40px; }
.cr-mode-label { font-family: "Space Grotesk"; font-weight: 700; font-size: 16px; }
.cr-mode-sub { font-size: 11px; color: var(--muted); text-align: center; line-height: 1.4; }

/* Status views */
.cr-big-status {
  position: relative;
  width: 160px; height: 160px;
  margin: 24px auto 8px;
  display: flex; align-items: center; justify-content: center;
}
.cr-pulse {
  position: absolute; inset: 0;
  border-radius: 50%;
  animation: pulsate 1.8s ease-in-out infinite;
}
.cr-pulse-red { background: radial-gradient(circle, var(--red-glow) 0%, transparent 70%); }
.cr-pulse-blue { background: radial-gradient(circle, rgba(59, 130, 246, 0.35) 0%, transparent 70%); }
@keyframes pulsate {
  0%, 100% { transform: scale(1); opacity: 0.7; }
  50%      { transform: scale(1.15); opacity: 1; }
}
.cr-status-icon { font-size: 60px; z-index: 1; }

.cr-spinner {
  width: 80px; height: 80px;
  border: 6px solid rgba(255, 255, 255, 0.1);
  border-top-color: var(--green);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.cr-check, .cr-cross {
  width: 120px; height: 120px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-family: "Space Grotesk";
  font-size: 64px; font-weight: 700;
}
.cr-check { background: rgba(1, 169, 130, 0.15); color: var(--green); border: 3px solid var(--green); }
.cr-cross { background: rgba(229, 62, 62, 0.15); color: var(--red); border: 3px solid var(--red); }

/* Transcript live */
.cr-transcript-live {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 16px;
  min-height: 140px;
  font-size: 15px; line-height: 1.55;
  margin: 12px 0;
}
.cr-transcript-final { color: var(--white); }
.cr-transcript-interim { color: var(--muted); font-style: italic; margin-top: 6px; }
.cr-transcript-hint { color: var(--dim); text-align: center; padding: 30px 0; }

/* Review cards */
.cr-card {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 12px;
  padding: 14px 16px;
}
.cr-card-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
  color: var(--muted); margin-bottom: 8px; font-weight: 600;
}
.cr-card-hint { font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--dim); }
.cr-card-value { font-size: 14px; }
.cr-accuracy { color: var(--dim); margin-left: 6px; font-size: 12px; }
.cr-map {
  width: 100%; height: 180px;
  border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.08);
  margin-top: 10px;
  filter: hue-rotate(180deg) invert(0.92);
}
.cr-textarea {
  width: 100%;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  color: var(--white);
  font-family: inherit;
  font-size: 14px;
  padding: 10px 12px;
  resize: vertical;
  outline: none;
}
.cr-textarea:focus {
  border-color: var(--green);
  box-shadow: 0 0 0 3px var(--green-glow);
}

.cr-btn-primary {
  width: 100%;
  padding: 16px;
  border: none;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--green), #00c9a1);
  color: var(--white);
  font-family: inherit;
  font-weight: 600;
  font-size: 15px;
  cursor: pointer;
  box-shadow: 0 4px 20px var(--green-glow);
  transition: 0.2s;
}
.cr-btn-primary:active { transform: scale(0.98); }
.cr-btn-primary.big { padding: 20px; font-size: 17px; }
.cr-btn-ghost {
  width: 100%;
  padding: 14px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  background: transparent;
  color: var(--muted);
  font-family: inherit;
  font-size: 14px;
  cursor: pointer;
}

.cr-footer {
  padding: 16px 20px;
  text-align: center;
  color: var(--dim);
  font-size: 11px;
  border-top: 1px solid rgba(255, 255, 255, 0.04);
}
</style>
