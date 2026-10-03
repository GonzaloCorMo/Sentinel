# Chatbot IA — Guia de uso

## Que es el chatbot

El chatbot de HPE Sentinel es un asistente inteligente que responde preguntas sobre el proyecto, la tecnologia utilizada y los protocolos operativos. Usa **RAG (Retrieval-Augmented Generation)** para buscar informacion relevante en una base de conocimiento y generar respuestas precisas.

## Como acceder

El chatbot aparece como un **panel flotante** en la esquina inferior izquierda de la aplicacion. Esta disponible en todas las vistas (mapa, flota, comunicaciones).

- **Abrir**: haz clic en el boton circular verde con el icono de chat.
- **Cerrar**: haz clic en la "X" del panel o en el boton circular de nuevo.

## Como usarlo

1. Escribe tu pregunta en el campo de texto del panel.
2. Pulsa Enter o haz clic en el boton de enviar.
3. La respuesta aparecera progresivamente en tiempo real (streaming).
4. El historial de la conversacion se mantiene entre recargas de pagina.

## Ejemplos de preguntas

### Sobre la tecnologia

- "¿Que tecnologias usa el frontend?"
- "¿Como funciona la comunicacion en tiempo real?"
- "¿Que base de datos se usa y como esta estructurada?"
- "¿Como se integra el motor de IA?"

### Sobre el funcionamiento

- "¿Como funciona la maquina de estados de las ambulancias?"
- "¿Que pasa cuando una ambulancia se queda sin combustible?"
- "¿Como se detectan las anomalias?"
- "¿Que es el modo HITL?"

### Sobre la arquitectura

- "¿Como se comunican Vue y FastAPI?"
- "¿Que es SSE y para que se usa?"
- "¿Como funciona el enrutamiento OSRM?"
- "¿Que hace Supabase en el proyecto?"

## Capacidades y limitaciones

### Lo que puede hacer

- Responder sobre arquitectura, tecnologías, protocolos y flujos de trabajo (modo RAG).
- Ejecutar **comandos estructurados** sobre la flota mediante tool-calling: filtrar unidades, centrar mapa, cambiar modo IA, resetear filtros.
- Persistir el historial de la sesión (`chat_messages` en Supabase, sessionId en localStorage).

### Comandos soportados (tool-calling)

| Comando | Ejemplo |
|---|---|
| `filter_units` | "muéstrame las ambulancias con combustible bajo" |
| `focus_unit` | "centra el mapa en AMB-003" |
| `set_ai_mode` | "pasa la IA a modo autónomo" |
| `reset_filters` | "limpia los filtros" |
| `explain` | (cualquier pregunta libre — pasa a RAG) |

Detrás del comando hay un fast-path regex (latencia <50 ms) y un fallback al LLM HPE-vLLM Gemma para casos ambiguos. El endpoint subyacente es `POST /api/ai/command` o `GET /ask?q=...`.

### Lo que no puede hacer

- No despacha emergencias por sí mismo (eso lo hace el panel HITL o el modo autónomo).
- No modifica la configuración del sistema.

## Configuración del proveedor de IA

| Componente | Proveedor | Endpoint default |
|---|---|---|
| Chat + tool-calling | HPE-vLLM Gemma (flash) | `http://10.10.48.10:8000/v1` (`google/gemma-4-31b-it`) |
| Razonamiento extenso (informes) | HPE-vLLM Qwen (flagship) | `http://10.10.48.10:8001/v1` (`Qwen/Qwen3-235B-A22B`) |
| Embeddings | Ollama local | `http://ollama:11434/v1` (`nomic-embed-text`) |

Solo Ollama corre en el host (perfiles compose `gpu-nvidia`, `gpu-amd`, `cpu`). El chat va siempre al endpoint HPE-vLLM externo, no requiere GPU local.

## Datos tecnicos

- Las respuestas se generan mediante un pipeline RAG que combina busqueda vectorial (pgvector en Supabase) con generacion de texto (LLM).
- La base de conocimiento se siembra automaticamente al arrancar el servidor con 18+ fragmentos de informacion sobre el proyecto.
- Las conversaciones se persisten en la tabla `chat_messages` de Supabase.
- Cada sesion tiene un ID unico almacenado en `localStorage`.
