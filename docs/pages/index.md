---
layout: home
title: Inicio

hero:
  name: Sentinel
  text: Gemelo digital de flota sanitaria
  tagline: Simulación de ambulancias en tiempo real, IA con supervisión humana o autónoma, fuente de eventos externos y pipeline ML. Documentación técnica y operativa.
  actions:
    - theme: brand
      text: Levantar el proyecto
      link: /getting-started/levantar-proyecto
    - theme: alt
      text: Introducción
      link: /intro
    - theme: alt
      text: API HTTP + SSE
      link: /technical/simulation-api-http-sse

features:
  - title: Motor de simulación
    details: FastAPI + asyncio. Cinco motores de telemetría por unidad, ETA dinámica, scoring de asignación y rutas OSRM multirregión (Aruba, Madrid, Bogotá, CDMX).
    link: /technical/simulador-global-despacho
    linkText: Simulador y despacho
  - title: IA HITL y autónoma
    details: Detección de anomalías, protocolos vía RAG (pgvector) y propuestas que aprueba el operador o ejecuta la IA directamente.
    link: /technical/ai-hitl-autonomo
    linkText: Motor de IA
  - title: Fuente de eventos
    details: Generador mock de incidentes y meteorología, más ingesta REST validada con Pydantic para fuentes reales.
    link: /technical/fuente-de-eventos
    linkText: Diseño de la fuente
  - title: Tiempo real por SSE
    details: El dashboard Vue 3 recibe el snapshot completo de la simulación a ~2,5 Hz por Server-Sent Events.
    link: /technical/simulation-api-http-sse
    linkText: Contrato de la API
  - title: Pipeline ML
    details: Captura de decisiones y outcomes, modo de entrenamiento autónomo y modelos ONNX servidos por ml-service.
    link: /ml/pipeline-entrenamiento
    linkText: Pipeline de entrenamiento
  - title: Operación
    details: Manual del operador, chatbot con comandos estructurados, panel del vehículo y PWA ciudadana.
    link: /guia-usuario/manual-de-uso
    linkText: Manual de uso
---
