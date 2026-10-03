/**
 * Nodos tácticos del mapa: geometría SVG pura, contorno de 1,5 px y relleno
 * oscuro al 90 %. La forma dice qué es y el color del borde, en qué estado está:
 *
 *   cuadrado  → infraestructura (hospital, gasolinera, lugar)
 *   rectángulo → vehículo
 *   rombo     → emergencia / alerta
 *
 * El color se reserva al borde: cian = unidad operativa, ámbar = aviso,
 * rojo = emergencia, gris claro = infraestructura, gris oscuro = apagado.
 * Devuelve HTML para `maplibregl.Marker({ element })`; los estilos viven en
 * `assets/main.css` (sección «Nodos tácticos»).
 */

export type NodeShape = "square" | "rect" | "diamond";
export type NodeTone = "unit" | "warn" | "crit" | "infra" | "off";
export type NodeGlyph = "none" | "cross" | "pip" | "dot";

/** Orden de gravedad para heredar el tono en un grupo (mayor = más grave). */
export const TONE_RANK: Record<NodeTone, number> = { off: 0, infra: 1, unit: 2, warn: 3, crit: 4 };

export const TONE_COLOR: Record<NodeTone, string> = {
  unit: "#5ec4d6",
  warn: "#ffb020",
  crit: "#ff2a2a",
  infra: "#c9ced6",
  off: "#6b7280",
};

export interface NodeOptions {
  shape: NodeShape;
  tone: NodeTone;
  /** Identificador corto; se muestra como `[AMB-001]`. Se escapa aquí. */
  label?: string;
  glyph?: NodeGlyph;
  /** Contorno discontinuo (p. ej. emergencia que llega de una fuente externa). */
  dashed?: boolean;
  /** Anillo expansivo: solo emergencias críticas. */
  ping?: boolean;
  /** Corchetes de esquina: unidad seleccionada. */
  selected?: boolean;
  /** Corchetes discontinuos: coincide con el filtro del asistente. */
  matched?: boolean;
}

const SIZE: Record<NodeShape, [number, number]> = {
  square: [14, 14],
  rect: [22, 12],
  diamond: [18, 18],
};

function esc(v: unknown): string {
  return String(v ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** Recorta indicativos largos: los nombres completos van en el tooltip. */
export function shortCallsign(label: string, max = 9): string {
  const s = label.trim().toUpperCase().replace(/\s+/g, "-");
  return s.length > max ? s.slice(0, max) : s;
}

function geometry(shape: NodeShape, w: number, h: number, dashed: boolean): string {
  const dash = dashed ? ' stroke-dasharray="2.5 2"' : "";
  if (shape === "diamond") {
    const pts = `${w / 2},0.75 ${w - 0.75},${h / 2} ${w / 2},${h - 0.75} 0.75,${h / 2}`;
    return `<polygon class="tn-shape" points="${pts}"${dash} />`;
  }
  return `<rect class="tn-shape" x="0.75" y="0.75" width="${w - 1.5}" height="${h - 1.5}" shape-rendering="crispEdges"${dash} />`;
}

function glyphSvg(glyph: NodeGlyph, w: number, h: number): string {
  const cx = w / 2;
  const cy = h / 2;
  switch (glyph) {
    case "cross":
      return `<path class="tn-glyph" d="M${cx} ${cy - 3}V${cy + 3}M${cx - 3} ${cy}H${cx + 3}" shape-rendering="crispEdges" />`;
    case "pip":
      return `<rect class="tn-pip" x="${cx - 2}" y="${cy - 2}" width="4" height="4" shape-rendering="crispEdges" />`;
    case "dot":
      return `<rect class="tn-pip" x="${cx - 1.5}" y="${cy - 1.5}" width="3" height="3" shape-rendering="crispEdges" />`;
    default:
      return "";
  }
}

/** Corchetes en las cuatro esquinas, 4 px fuera de la geometría. */
function brackets(w: number, h: number, cls: string): string {
  const p = 4;
  const L = 4;
  const W = w + p * 2;
  const H = h + p * 2;
  const d = [
    `M0.75 ${L}V0.75H${L}`,
    `M${W - L} 0.75H${W - 0.75}V${L}`,
    `M${W - 0.75} ${H - L}V${H - 0.75}H${W - L}`,
    `M${L} ${H - 0.75}H0.75V${H - L}`,
  ].join("");
  return `<svg class="${cls}" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="left:${-p}px;top:${-p}px" aria-hidden="true"><path d="${d}" shape-rendering="crispEdges" /></svg>`;
}

export function tacticalNodeHtml(o: NodeOptions): string {
  const [w, h] = SIZE[o.shape];
  const cls = ["tn", `tn-${o.shape}`, `tn-${o.tone}`].join(" ");
  const ping = o.ping ? '<span class="tn-ping" aria-hidden="true"></span>' : "";
  const sel = o.selected ? brackets(w, h, "tn-brackets") : o.matched ? brackets(w, h, "tn-brackets tn-brackets-matched") : "";
  const label = o.label ? `<span class="tn-label">[${esc(shortCallsign(o.label))}]</span>` : "";
  return `<div class="${cls}" style="--tn:${TONE_COLOR[o.tone]};width:${w}px;height:${h}px">${ping}<svg class="tn-geo" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${geometry(o.shape, w, h, !!o.dashed)}${glyphSvg(o.glyph ?? "none", w, h)}</svg>${sel}${label}</div>`;
}

/** Nodo de agrupación: marco doble con el contador en monoespaciada. */
export function clusterNodeHtml(count: number, tone: NodeTone, ping = false): string {
  const s = count > 99 ? 30 : 24;
  const pingHtml = ping ? '<span class="tn-ping" aria-hidden="true"></span>' : "";
  return `<div class="tn tn-cluster tn-${tone}" style="--tn:${TONE_COLOR[tone]};width:${s}px;height:${s}px">${pingHtml}<svg class="tn-geo" width="${s}" height="${s}" viewBox="0 0 ${s} ${s}" aria-hidden="true"><rect class="tn-shape" x="0.75" y="0.75" width="${s - 1.5}" height="${s - 1.5}" shape-rendering="crispEdges" /><rect class="tn-frame" x="3.5" y="3.5" width="${s - 7}" height="${s - 7}" shape-rendering="crispEdges" /></svg><span class="tn-count">${count}</span></div>`;
}
