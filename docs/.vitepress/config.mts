import { defineConfig } from 'vitepress'

export default defineConfig({
  srcDir: 'pages',
  lang: 'es-ES',
  title: 'Sentinel — Digital Twin Docs',
  titleTemplate: ':title · Sentinel',
  description:
    'Documentación técnica de Sentinel, gemelo digital de una flota de ambulancias: simulación, IA HITL/autónoma, fuente de eventos y pipeline ML.',
  appearance: 'dark',
  cleanUrls: true,
  lastUpdated: true,
  // Los únicos enlaces que no se pueden resolver en build son URLs de servicios locales.
  ignoreDeadLinks: [/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?/],

  head: [
    ['link', { rel: 'preconnect', href: 'https://fonts.googleapis.com' }],
    ['link', { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' }],
    [
      'link',
      {
        rel: 'stylesheet',
        href: 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap',
      },
    ],
    ['meta', { name: 'theme-color', content: '#0b0c0e' }],
  ],

  themeConfig: {
    siteTitle: 'Sentinel Docs',

    nav: [
      { text: 'Guía', link: '/getting-started/levantar-proyecto', activeMatch: '/getting-started/' },
      { text: 'Técnico', link: '/technical/simulation-api-http-sse', activeMatch: '/technical/' },
      { text: 'ML', link: '/ml/pipeline-entrenamiento', activeMatch: '/ml/' },
      { text: 'Manual', link: '/guia-usuario/manual-de-uso', activeMatch: '/guia-usuario/' },
    ],

    sidebar: [
      {
        text: 'Inicio',
        items: [
          { text: 'Portada', link: '/' },
          { text: 'Introducción', link: '/intro' },
        ],
      },
      {
        text: 'Getting Started',
        items: [
          { text: 'Levantar el proyecto', link: '/getting-started/levantar-proyecto' },
          { text: 'Arranque con Docker', link: '/getting-started/docker' },
          { text: 'Probar la PWA en móvil', link: '/getting-started/pwa-mobile' },
        ],
      },
      {
        text: 'Auth',
        items: [{ text: 'Supabase Auth (fase 1)', link: '/auth/supabase-auth-fase-1' }],
      },
      {
        text: 'Arquitectura',
        items: [
          { text: 'Vue + FastAPI', link: '/arquitectura/arquitectura-hibrida-ts-python' },
          { text: 'Contratos operativos', link: '/arquitectura/contratos-operativos-next-python' },
        ],
      },
      {
        text: 'Técnico',
        items: [
          { text: 'API de simulación (HTTP + SSE)', link: '/technical/simulation-api-http-sse' },
          { text: 'Modelo de simulación', link: '/technical/modelo-de-simulacion' },
          { text: 'Fuente de eventos', link: '/technical/fuente-de-eventos' },
          { text: 'Simulador y despacho', link: '/technical/simulador-global-despacho' },
          { text: 'Base de datos', link: '/technical/base-de-datos' },
          { text: 'OSRM', link: '/technical/osrm-local-docker' },
          { text: 'IA: HITL y autónomo', link: '/technical/ai-hitl-autonomo' },
          { text: 'IA: chatbot RAG', link: '/technical/ai-chatbot-rag' },
          { text: 'Runbook de resiliencia', link: '/technical/runbook-resiliencia-operativa' },
          { text: 'Trazabilidad técnica', link: '/technical/trazabilidad-tecnica' },
        ],
      },
      {
        text: 'ML',
        items: [
          { text: 'Pipeline de entrenamiento', link: '/ml/pipeline-entrenamiento' },
          { text: 'Modo simulación autónoma', link: '/ml/modo-simulacion-autonoma' },
          { text: 'Tiempo de entrenamiento', link: '/ml/tiempo-entrenamiento' },
          { text: 'Usar el modelo entrenado', link: '/ml/usar-modelo-entrenado' },
          { text: 'Proyecto paralelo de training', link: '/ml/proyecto-paralelo-ml-training' },
        ],
      },
      {
        text: 'Guía de usuario',
        items: [
          { text: 'Manual de uso', link: '/guia-usuario/manual-de-uso' },
          { text: 'Chatbot IA', link: '/guia-usuario/chatbot-ia' },
          { text: 'Operación de despacho', link: '/guia-usuario/operacion-real-despacho' },
        ],
      },
    ],

    outline: { level: [2, 3], label: 'En esta página' },
    search: {
      provider: 'local',
      options: {
        translations: {
          button: { buttonText: 'Buscar', buttonAriaLabel: 'Buscar' },
          modal: {
            noResultsText: 'Sin resultados para',
            resetButtonTitle: 'Limpiar búsqueda',
            footer: { selectText: 'seleccionar', navigateText: 'navegar', closeText: 'cerrar' },
          },
        },
      },
    },
    lastUpdated: { text: 'Última actualización' },
    docFooter: { prev: 'Anterior', next: 'Siguiente' },
    darkModeSwitchLabel: 'Apariencia',
    lightModeSwitchTitle: 'Cambiar a modo claro',
    darkModeSwitchTitle: 'Cambiar a modo oscuro',
    sidebarMenuLabel: 'Menú',
    returnToTopLabel: 'Volver arriba',
    langMenuLabel: 'Idioma',
    notFound: {
      title: 'Página no encontrada',
      quote: 'La ruta solicitada no existe o se ha movido.',
      linkText: 'Volver al inicio',
    },
  },
})
