# Chatbot IA: guía de uso

## Qué es el chatbot

El chatbot de Sentinel es un asistente inteligente que responde preguntas sobre el proyecto, la tecnología utilizada y los protocolos operativos. Usa **RAG (Retrieval-Augmented Generation)** para buscar información relevante en una base de conocimiento y generar respuestas precisas.

## Cómo acceder

El chatbot aparece como un **panel flotante** en la esquina inferior izquierda de la aplicación. Está disponible en todas las secciones (Mapa, Panorama, Flota, Comunicaciones, Informes).

- **Abrir**: haz clic en el botón circular con el icono de chat.
- **Cerrar**: haz clic en la "X" del panel o en el botón circular de nuevo.

## Cómo usarlo

El asistente tiene dos modos, que eliges en el propio panel:

- **Preguntar**: responde dudas sobre el proyecto, la tecnología y los protocolos buscando en la base de conocimiento (RAG).
- **Dar una orden**: interpreta lo que escribes como un comando sobre la flota o el mapa (ver [Comandos soportados](#comandos-soportados-tool-calling)).

Pasos:

1. Elige el modo y escribe tu pregunta u orden en el campo de texto del panel.
2. Pulsa Enter o haz clic en el botón de enviar.
3. La respuesta aparecerá progresivamente en tiempo real (streaming).
4. El historial de la conversación se mantiene entre recargas de página.

## Ejemplos de preguntas

### Sobre la tecnología

- "¿Qué tecnologías usa el frontend?"
- "¿Cómo funciona la comunicación en tiempo real?"
- "¿Qué base de datos se usa y cómo está estructurada?"
- "¿Cómo se integra el motor de IA?"

### Sobre el funcionamiento

- "¿Cómo funciona la máquina de estados de las ambulancias?"
- "¿Qué pasa cuando una ambulancia se queda sin combustible?"
- "¿Cómo se detectan las anomalías?"
- "¿Qué es el modo HITL?"

### Sobre la arquitectura

- "¿Cómo se comunican Vue y FastAPI?"
- "¿Qué es SSE y para qué se usa?"
- "¿Cómo funciona el enrutamiento OSRM?"
- "¿Qué hace Supabase en el proyecto?"

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

Detrás del comando hay un fast-path regex (latencia <50 ms) y un fallback al LLM local (Ollama, `qwen2.5:3b` por defecto) para casos ambiguos. El endpoint subyacente es `POST /api/ai/command`.

### Lo que no puede hacer

- No despacha emergencias por sí mismo (eso lo hace el panel HITL o el modo autónomo).
- No modifica la configuración del sistema.

## Configuración del proveedor de IA

| Componente | Proveedor | Endpoint default |
|---|---|---|
| Chat + tool-calling | Ollama local (flash) | `http://ollama:11434/v1` (`qwen2.5:3b`) |
| Informes de turno e IA observadora | Ollama local (flagship) | `http://ollama:11434/v1` (`qwen2.5:3b`) |
| Embeddings | Ollama local | `http://ollama:11434/v1` (`nomic-embed-text`) |

Toda la IA corre en local dentro del stack (perfiles compose `gpu-nvidia`, `gpu-amd`, `cpu`). Con GPU las respuestas son mucho más rápidas. Cómo cambiar de modelo o de servidor: [IA local con Ollama](../technical/ai-chatbot-rag.md#ia-local-con-ollama).

## Datos técnicos

Pipeline, tablas y variables de entorno en [Chatbot RAG](../technical/ai-chatbot-rag.md).
