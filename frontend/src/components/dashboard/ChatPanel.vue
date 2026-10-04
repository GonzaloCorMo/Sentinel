<script setup lang="ts">
import { marked } from "marked";
import { sanitizeHtml } from "@/lib/sanitize";
import { computed, nextTick, onMounted, ref } from "vue";
import { toast } from "vue-sonner";
import { useI18n } from "vue-i18n";
import { useSimulationStore } from "@/stores/simulation";

const { t } = useI18n();
const simStore = useSimulationStore();

interface ChatMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
}

const expanded = ref(false);
const input = ref("");
const messages = ref<ChatMessage[]>([]);
const streaming = ref(false);
const chatBody = ref<HTMLDivElement | null>(null);

const SESSION_KEY = "sentinel.chat-session";

// crypto.randomUUID() requires a secure context (https/localhost). When the
// dashboard is opened over a LAN IP without TLS the browser does not expose
// it, so fall back to getRandomValues (available everywhere) and finally to
// Math.random as a last resort.
function genUuidV4(): string {
  const g = globalThis.crypto;
  if (g && typeof g.randomUUID === "function") return g.randomUUID();
  const bytes = new Uint8Array(16);
  if (g && typeof g.getRandomValues === "function") {
    g.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10
  const hex: string[] = [];
  for (let i = 0; i < 16; i++) hex.push(bytes[i].toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`;
}

function getSessionId(): string {
  let sid = localStorage.getItem(SESSION_KEY);
  if (!sid) {
    sid = genUuidV4();
    localStorage.setItem(SESSION_KEY, sid);
  }
  return sid;
}

function renderMd(text: string): string {
  return sanitizeHtml(marked.parse(text, { async: false }) as string);
}

const hasMessages = computed(() => messages.value.length > 0);

async function scrollToBottom() {
  await nextTick();
  if (chatBody.value) {
    chatBody.value.scrollTop = chatBody.value.scrollHeight;
  }
}

async function loadHistory() {
  try {
    const res = await fetch(`/api/chat/history?sessionId=${getSessionId()}`);
    if (res.ok) {
      const data = await res.json();
      messages.value = data.map((m: any) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        created_at: m.created_at,
      }));
      await scrollToBottom();
    }
  } catch {
    // ignore
  }
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text || streaming.value) return;

  input.value = "";
  if (commandMode.value) {
    await runCommand(text);
    return;
  }
  messages.value.push({ role: "user", content: text });
  await scrollToBottom();

  streaming.value = true;
  const assistantMsg: ChatMessage = { role: "assistant", content: "" };
  messages.value.push(assistantMsg);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, sessionId: getSessionId() }),
    });

    if (!res.ok || !res.body) {
      assistantMsg.content = "Error al conectar con el asistente.";
      streaming.value = false;
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6);
        try {
          const parsed = JSON.parse(jsonStr);
          if (parsed.chunk) {
            assistantMsg.content += parsed.chunk;
            await scrollToBottom();
          }
        } catch {
          // ignore parse errors
        }
      }
    }
  } catch (err) {
    console.error("[ChatPanel] /api/chat stream failed:", err);
    const detail = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
    assistantMsg.content = assistantMsg.content || `Error de conexion. (${detail})`;
  } finally {
    streaming.value = false;
    await scrollToBottom();
  }
}

// ── Modo comando: interpreta con tool-calling y aplica efectos ─────────
const commandMode = ref(false);

async function runCommand(text: string) {
  messages.value.push({ role: "user", content: text });
  await scrollToBottom();
  streaming.value = true;
  const assistantMsg: ChatMessage = { role: "assistant", content: "⌘ interpretando…" };
  messages.value.push(assistantMsg);
  try {
    const res = await fetch("/api/ai/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    const cmd = data.command as string;
    const args = data.args || {};
    const summary = data.summary || cmd;

    // Mapa de etiquetas humanas por comando
    const cmdMeta: Record<string, { icon: string; label: string }> = {
      filter_units:   { icon: "", label: "Filtro aplicado" },
      reset_filters:  { icon: "", label: "Filtros limpiados" },
      set_ai_mode:    { icon: "", label: "Modo IA" },
      focus_unit:     { icon: "", label: "Enfocando unidad" },
      spawn_units:    { icon: "", label: "Desplegando unidades" },
      create_emergency: { icon: "", label: "Nueva emergencia" },
      explain:        { icon: "", label: "" },
    };
    const meta = cmdMeta[cmd] || { icon: "", label: cmd };

    // Ejecuta efecto cliente según comando
    let effectDetail = "";
    if (cmd === "filter_units") {
      simStore.setUiFilters(args);
      // Cuenta cuántas unidades cumplen para feedback honesto
      const ui = args as Record<string, unknown>;
      const matchFn = (a: { fuelLevel?: number | null; entityTypeId?: string; hasPatient?: boolean; patientSeverity?: string; missionPhase?: string | null; poweredOff?: boolean; telemetry?: { mechanical?: { batteryPct?: number } } }) => {
        const type = a.entityTypeId || "ambulance";
        if (ui.entityTypeId && type !== ui.entityTypeId) return false;
        if (ui.fuelBelow != null && (a.fuelLevel ?? 100) >= Number(ui.fuelBelow)) return false;
        if (ui.fuelAbove != null && (a.fuelLevel ?? 0) <= Number(ui.fuelAbove)) return false;
        if (ui.batteryBelow != null && (a.telemetry?.mechanical?.batteryPct ?? 100) >= Number(ui.batteryBelow)) return false;
        if (ui.hasPatient != null && !!a.hasPatient !== ui.hasPatient) return false;
        if (ui.severity && a.patientSeverity !== ui.severity) return false;
        if (ui.missionPhase && (a.missionPhase || "idle") !== ui.missionPhase) return false;
        if (ui.poweredOff != null && !!a.poweredOff !== ui.poweredOff) return false;
        return true;
      };
      const total = simStore.state?.ambulances?.length ?? 0;
      const match = (simStore.state?.ambulances ?? []).filter(matchFn).length;
      toast.success(t("chat.filter_toast", { match, total }));
      effectDetail = match === 0
        ? t("chat.filter_none", { total })
        : t("chat.filter_some", { match, total });
    } else if (cmd === "reset_filters") {
      simStore.clearUiFilters();
      toast.info(t("chat.filters_cleared"));
    } else if (cmd === "set_ai_mode") {
      await simStore.setAiMode(args.mode);
      toast.success(`IA → ${args.mode}`);
      effectDetail = args.mode === "autonomous"
        ? "IA ejecuta decisiones sin aprobación humana."
        : "IA propone; operador aprueba cada acción.";
    } else if (cmd === "focus_unit") {
      const q = String(args.query || "").toLowerCase();
      const amb = simStore.state?.ambulances.find(
        (a) => a.id.includes(q) || (a.displayLabel || "").toLowerCase().includes(q),
      );
      if (amb) {
        simStore.selectAmbulance(amb.id);
        toast.success(`Enfocada ${amb.displayLabel || amb.id.slice(0, 6)}`);
        effectDetail = `Unidad seleccionada en el panel lateral.`;
      } else {
        toast.warning("No se encontró la unidad");
        effectDetail = "No se encontró ninguna unidad que coincida.";
      }
    } else if (cmd === "explain") {
      assistantMsg.content = args.text || summary;
      return;
    } else {
      toast.info(summary);
    }

    // Mensaje compacto legible — sin JSON pelado
    assistantMsg.content = [
      `**${meta.label || cmd}**`,
      summary,
      effectDetail,
    ].filter(Boolean).join("\n\n");
  } catch {
    assistantMsg.content = "Error procesando el comando.";
  } finally {
    streaming.value = false;
    await scrollToBottom();
  }
}

function clearChat() {
  messages.value = [];
  localStorage.removeItem(SESSION_KEY);
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

onMounted(loadHistory);
</script>

<template>
  <div class="fixed bottom-4 left-4 z-[500] flex flex-col items-start gap-2">
    <!-- Collapsed bubble -->
    <button
      v-if="!expanded"
      class="group relative flex h-10 w-10 items-center justify-center rounded border border-slate-700 bg-slate-100 text-slate-950 transition-colors hover:bg-slate-300"
      :title="t('chat_panel.open')"
      :aria-label="t('chat_panel.open')"
      @click="expanded = true"
    >
      <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
      </svg>
      <span v-if="hasMessages" class="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-slate-100 ring-2 ring-slate-950" aria-hidden="true" />
    </button>

    <!-- Expanded panel -->
    <div
      v-if="expanded"
      class="flex w-[380px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded border border-slate-700 bg-slate-900 shadow-xl"
      style="max-height: min(520px, 70vh)"
    >
      <!-- Header -->
      <div class="flex items-center justify-between border-b border-slate-800 px-4 py-2">
        <div>
          <p class="text-sm font-medium text-slate-100">{{ t('chat_panel.title') }}</p>
          <p class="text-[11px] text-slate-500">{{ t('chat_panel.local_model') }}</p>
        </div>
        <div class="flex items-center gap-1">
          <button
            class="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-800 hover:text-slate-300"
            :title="t('chat_panel.clear')"
            @click="clearChat"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
          <button
            class="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-800 hover:text-slate-300"
            :title="t('chat_panel.close')"
            @click="expanded = false"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Messages body -->
      <div ref="chatBody" class="flex-1 overflow-y-auto px-3 py-3 space-y-3" style="min-height: 200px">
        <p v-if="!messages.length" class="px-2 py-6 text-center text-xs leading-relaxed text-slate-500">
          {{ commandMode ? t('chat_panel.empty_command') : t('chat_panel.empty') }}
        </p>

        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="flex"
          :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
        >
          <div
            class="max-w-[85%] rounded px-3 py-2 text-sm leading-relaxed"
            :class="msg.role === 'user' ? 'bg-slate-100 text-slate-950' : 'border border-slate-800 bg-slate-950 text-slate-200'"
          >
            <div v-if="msg.role === 'assistant'" class="chat-md prose prose-sm prose-invert max-w-none" v-html="renderMd(msg.content || '...')" />
            <span v-else>{{ msg.content }}</span>
          </div>
        </div>

        <div v-if="streaming" class="flex items-center gap-1.5 text-xs text-slate-500">
          <span class="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400" />
          {{ t('chat_panel.thinking') }}
        </div>
      </div>

      <!-- Input -->
      <div class="border-t border-slate-800 px-3 py-2.5">
        <div class="mb-2 flex rounded border border-slate-800 p-0.5 text-[11px]" role="tablist">
          <button
            type="button"
            role="tab"
            :aria-selected="!commandMode"
            class="flex-1 rounded-sm px-2 py-1"
            :class="!commandMode ? 'bg-slate-800 text-slate-100' : 'text-slate-500 hover:text-slate-200'"
            :title="t('chat_panel.mode_ask_hint')"
            @click="commandMode = false"
          >{{ t('chat_panel.mode_ask') }}</button>
          <button
            type="button"
            role="tab"
            :aria-selected="commandMode"
            class="flex-1 rounded-sm px-2 py-1"
            :class="commandMode ? 'bg-slate-800 text-slate-100' : 'text-slate-500 hover:text-slate-200'"
            :title="t('chat_panel.mode_command_hint')"
            @click="commandMode = true"
          >{{ t('chat_panel.mode_command') }}</button>
        </div>
        <div class="flex items-end gap-2">
          <textarea
            v-model="input"
            rows="1"
            class="flex-1 resize-none rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-slate-500"
            :placeholder="commandMode ? t('ai.command_placeholder') : t('chat_panel.placeholder')"
            :disabled="streaming"
            @keydown="handleKeydown"
          />
          <button
            :disabled="streaming || !input.trim()"
            class="flex h-9 w-9 items-center justify-center rounded bg-slate-100 text-slate-950 hover:bg-slate-300 disabled:opacity-30"
            :aria-label="t('chat_panel.send')"
            @click="sendMessage"
          >
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 19V5m-7 7l7-7 7 7" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-md :deep(p) {
  margin: 0.25em 0;
}
.chat-md :deep(ul), .chat-md :deep(ol) {
  padding-left: 1.2em;
  margin: 0.25em 0;
}
.chat-md :deep(pre) {
  background: var(--bg);
  color: var(--text);
  padding: 0.5em 0.75em;
  border-radius: 0.5rem;
  border: 1px solid var(--border);
  overflow-x: auto;
  font-size: 0.8em;
  margin: 0.5em 0;
}
.chat-md :deep(code) {
  font-size: 0.85em;
}
.chat-md :deep(a) {
  color: var(--text);
  text-decoration: underline;
}
</style>
