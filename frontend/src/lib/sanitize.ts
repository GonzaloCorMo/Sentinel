/**
 * Saneado de HTML que se renderiza con v-html o dentro de iconos de Leaflet.
 *
 * - Markdown del chat: lo genera un LLM alimentado con datos externos
 *   (RAG, eventos ingeridos), así que se trata como no confiable.
 * - SVG de tipos de entidad: los sube el usuario o los propone la IA.
 */
import DOMPurify from "dompurify";

export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}

export function sanitizeSvg(svg: string | null | undefined): string {
  if (!svg) return "";
  return DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true } });
}
