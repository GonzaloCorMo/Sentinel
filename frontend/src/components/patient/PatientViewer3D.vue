<script setup lang="ts">
/**
 * Visor 3D del paciente: malla de alambre monocroma con la zona afectada
 * resaltada (rojo crítico / ámbar aviso), barrido de escáner y etiquetas
 * HTML ancladas a la zona. three.js directo (el proyecto es Vue, no React).
 *
 * Modelo: «Male base» de Артур Мигранов (Poly Pizza, CC BY). Si no carga,
 * se usa un maniquí de primitivas en el mismo espacio de coordenadas.
 */
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import type { Zone } from "@/lib/patientZones";

const props = defineProps<{
  zones: Zone[];
  tone: "crit" | "warn";
  /** Texto de cada etiqueta, por zona (ya traducido). */
  labels: Record<string, { zone: string; detail: string }>;
  /** Descripción accesible del lienzo. */
  description: string;
}>();

const { t } = useI18n();
const MODEL_URL = "/models/male-base.glb";
const MODEL_HEIGHT = 20.7;
const MAX_ZONES = 4;
const ALERT = { crit: new THREE.Color("#ff2a2a"), warn: new THREE.Color("#ffb020") };

const host = ref<HTMLDivElement | null>(null);
const usingFallback = ref(false);

let renderer: THREE.WebGLRenderer | null = null;
let labelRenderer: CSS2DRenderer | null = null;
let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let controls: OrbitControls;
let body: THREE.Group;
let overlay: THREE.Group;
let raf = 0;
let resumeTimer = 0;
let ro: ResizeObserver | null = null;
let themeObs: MutationObserver | null = null;
const clock = new THREE.Clock();

const uniforms = {
  uBase: { value: new THREE.Color("#8b96a5") },
  uAlert: { value: ALERT.warn.clone() },
  uZones: { value: Array.from({ length: MAX_ZONES }, () => new THREE.Vector4(0, -100, 0, 0.001)) },
  uCount: { value: 0 },
  uTime: { value: 0 },
  uPulse: { value: 0 },
  uScanY: { value: 0 },
  uOpacity: { value: 0.16 },
};

const VERT = /* glsl */ `
  varying vec3 vLocal;
  void main() {
    vLocal = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const FRAG = /* glsl */ `
  uniform vec3 uBase;
  uniform vec3 uAlert;
  uniform vec4 uZones[${MAX_ZONES}];
  uniform int uCount;
  uniform float uTime;
  uniform float uPulse;
  uniform float uScanY;
  uniform float uOpacity;
  varying vec3 vLocal;
  void main() {
    float g = 0.0;
    for (int i = 0; i < ${MAX_ZONES}; i++) {
      if (i >= uCount) break;
      float d = distance(vLocal, uZones[i].xyz) / uZones[i].w;
      g = max(g, 1.0 - smoothstep(0.45, 1.0, d));
    }
    float pulse = mix(1.0, 0.72 + 0.28 * sin(uTime * 3.2), uPulse);
    float scan = (1.0 - smoothstep(0.0, 0.45, abs(vLocal.y - uScanY))) * 0.5;
    vec3 col = mix(uBase, uAlert, g) + uAlert * g * 0.35 * pulse;
    float a = uOpacity * (1.0 + scan) + g * pulse * (1.0 - uOpacity);
    gl_FragColor = vec4(col, clamp(a, 0.0, 1.0));
  }
`;

function makeMaterials() {
  const wire = new THREE.ShaderMaterial({
    uniforms, vertexShader: VERT, fragmentShader: FRAG, wireframe: true, transparent: true, depthWrite: false,
  });
  // Relleno casi invisible que da volumen. Estilo rayos X (sin escribir
  // profundidad): la zona afectada se ve también con el cuerpo de espaldas.
  const fill = new THREE.ShaderMaterial({
    uniforms: { ...uniforms, uOpacity: { value: 0.04 } },
    vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false,
  });
  return { wire, fill };
}

function addMesh(geometry: THREE.BufferGeometry, target: THREE.Group) {
  const { wire, fill } = makeMaterials();
  target.add(new THREE.Mesh(geometry, fill));
  target.add(new THREE.Mesh(geometry, wire));
}

/** Maniquí de primitivas en las coordenadas del modelo (pies en 0, altura ~20,7). */
function buildMannequin(target: THREE.Group) {
  const parts: Array<[THREE.BufferGeometry, [number, number, number], [number, number, number]?]> = [
    [new THREE.SphereGeometry(1.15, 18, 14), [0, 19.4, 0]],
    [new THREE.CylinderGeometry(0.5, 0.55, 1.0, 12), [0, 17.9, 0]],
    [new THREE.CylinderGeometry(2.0, 1.6, 5.0, 16), [0, 14.9, 0]],
    [new THREE.CylinderGeometry(1.6, 1.8, 2.6, 16), [0, 11.1, 0]],
    [new THREE.CylinderGeometry(0.45, 0.35, 4.2, 10), [2.9, 14.3, 0], [0, 0, 0.42]],
    [new THREE.CylinderGeometry(0.45, 0.35, 4.2, 10), [-2.9, 14.3, 0], [0, 0, -0.42]],
    [new THREE.CylinderGeometry(0.35, 0.28, 3.6, 10), [4.7, 11.2, 0], [0, 0, 0.42]],
    [new THREE.CylinderGeometry(0.35, 0.28, 3.6, 10), [-4.7, 11.2, 0], [0, 0, -0.42]],
    [new THREE.CylinderGeometry(0.75, 0.55, 5.0, 12), [1.1, 7.2, 0]],
    [new THREE.CylinderGeometry(0.75, 0.55, 5.0, 12), [-1.1, 7.2, 0]],
    [new THREE.CylinderGeometry(0.55, 0.4, 4.4, 12), [1.1, 2.4, 0]],
    [new THREE.CylinderGeometry(0.55, 0.4, 4.4, 12), [-1.1, 2.4, 0]],
  ];
  for (const [geo, pos, rot] of parts) {
    if (rot) geo.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(...rot)));
    geo.translate(...pos);
    addMesh(geo, target);
  }
}

function esc(v: unknown): string {
  return String(v ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** Marcadores, líneas guía y etiquetas de las zonas afectadas. */
function rebuildOverlay() {
  if (!overlay) return;
  for (const child of [...overlay.children]) {
    overlay.remove(child);
    if (child instanceof CSS2DObject) child.element.remove();
    const m = child as THREE.Mesh;
    m.geometry?.dispose?.();
    (m.material as THREE.Material | undefined)?.dispose?.();
  }
  const color = ALERT[props.tone];
  uniforms.uAlert.value.copy(color);
  uniforms.uPulse.value = props.tone === "crit" ? 1 : 0;
  uniforms.uCount.value = Math.min(MAX_ZONES, props.zones.length);
  props.zones.slice(0, MAX_ZONES).forEach((z, i) => {
    uniforms.uZones.value[i].set(z.center[0], z.center[1], z.center[2], z.radius);
    const c = new THREE.Vector3(...z.center);
    // Punto de anclaje: hacia el lado de la zona, alternando si está centrada.
    const side = Math.abs(z.center[0]) > 0.3 ? Math.sign(z.center[0]) : i % 2 === 0 ? 1 : -1;
    const anchor = new THREE.Vector3(side * 10.5, z.center[1] + 1.4 - i * 1.6, z.center[2]);
    const lineMat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 });
    const elbow = new THREE.Vector3(side * 7.4, anchor.y, z.center[2]);
    overlay.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([c, elbow, anchor]), lineMat));
    const dot = new THREE.Mesh(new THREE.OctahedronGeometry(0.28), new THREE.MeshBasicMaterial({ color, wireframe: true }));
    dot.position.copy(c);
    dot.userData.spin = true;
    overlay.add(dot);
    const text = props.labels[z.id] ?? { zone: z.id, detail: "" };
    // three.js escribe la transformación del elemento exterior en cada frame;
    // el desplazamiento lateral va en el interior para no pelearse con ella.
    const el = document.createElement("div");
    el.className = "pv-anchor";
    el.innerHTML = `<div class="pv-label pv-${props.tone} ${side > 0 ? "pv-left" : "pv-right"}"><span class="pv-zone">[${esc(text.zone)}]</span>${text.detail ? `<span class="pv-detail">${esc(text.detail)}</span>` : ""}</div>`;
    const label = new CSS2DObject(el);
    label.position.copy(anchor);
    overlay.add(label);
  });
}

function applyTheme() {
  const dark = document.documentElement.dataset.theme !== "light";
  uniforms.uBase.value.set(dark ? "#8b96a5" : "#4b5563");
}

function resize() {
  if (!host.value || !renderer || !labelRenderer) return;
  const w = host.value.clientWidth;
  const h = host.value.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  labelRenderer.setSize(w, h);
  camera.aspect = w / h;
  // En paneles estrechos se aleja la cámara para que quepan las etiquetas.
  camera.position.setLength(w / h < 1 ? 6.4 : 4.4);
  camera.updateProjectionMatrix();
}

function frame() {
  raf = requestAnimationFrame(frame);
  const t = clock.getElapsedTime();
  uniforms.uTime.value = t;
  uniforms.uScanY.value = (t * 3.2) % (MODEL_HEIGHT + 2) - 1;
  for (const o of overlay.children) if (o.userData.spin) o.rotation.y = t * 1.2;
  controls.update();
  renderer!.render(scene, camera);
  labelRenderer!.render(scene, camera);
  snapLabelsToPixels();
}

/** CSS2DRenderer usa posiciones fraccionarias: el texto «tiembla» al girar. Se redondean. */
function snapLabelsToPixels() {
  for (const o of overlay.children) {
    if (!(o instanceof CSS2DObject)) continue;
    const st = o.element.style;
    const snapped = st.transform.replace(/(-?\d+\.\d+)px/g, (_, n: string) => `${Math.round(Number(n))}px`);
    if (snapped !== st.transform) st.transform = snapped;
  }
}

onMounted(() => {
  const el = host.value;
  if (!el) return;
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
  camera.position.set(0, 0.1, 4.4);
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0);
  el.appendChild(renderer.domElement);
  labelRenderer = new CSS2DRenderer();
  labelRenderer.domElement.className = "pv-labels";
  el.appendChild(labelRenderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enablePan = false;
  controls.enableZoom = true;
  controls.minDistance = 3.6;
  controls.maxDistance = 9;
  controls.minPolarAngle = Math.PI * 0.36;
  controls.maxPolarAngle = Math.PI * 0.62;
  controls.enableDamping = true;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.7;
  controls.addEventListener("start", () => {
    controls.autoRotate = false;
    window.clearTimeout(resumeTimer);
  });
  controls.addEventListener("end", () => {
    resumeTimer = window.setTimeout(() => (controls.autoRotate = true), 6000);
  });

  // Modelo normalizado: 2 unidades de alto, centrado en el origen.
  const root = new THREE.Group();
  root.scale.setScalar(2 / MODEL_HEIGHT);
  root.position.y = -1;
  body = new THREE.Group();
  overlay = new THREE.Group();
  root.add(body, overlay);
  scene.add(root);

  new GLTFLoader().load(
    MODEL_URL,
    (gltf) => {
      gltf.scene.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (mesh.isMesh) addMesh(mesh.geometry, body);
      });
    },
    undefined,
    () => {
      usingFallback.value = true;
      buildMannequin(body);
    },
  );

  applyTheme();
  themeObs = new MutationObserver(applyTheme);
  themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  rebuildOverlay();
  ro = new ResizeObserver(resize);
  ro.observe(el);
  resize();
  frame();
});

// Solo se reconstruye si cambia el contenido: el estado llega cada 0,4 s con
// objetos nuevos aunque la afección sea la misma, y recrear las etiquetas en
// cada actualización las hacía parpadear.
const overlayKey = () =>
  JSON.stringify([props.tone, props.zones.map((z) => [z.id, z.center, z.radius]), props.labels]);
watch(overlayKey, rebuildOverlay);

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  window.clearTimeout(resumeTimer);
  ro?.disconnect();
  themeObs?.disconnect();
  controls?.dispose();
  scene?.traverse((o) => {
    const m = o as THREE.Mesh;
    m.geometry?.dispose?.();
    const mat = m.material as THREE.Material | THREE.Material[] | undefined;
    (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((x) => x.dispose());
  });
  renderer?.dispose();
  renderer?.domElement.remove();
  labelRenderer?.domElement.remove();
  renderer = null;
  labelRenderer = null;
});
</script>

<template>
  <div ref="host" class="pv-host" role="img" :aria-label="description">
    <p class="pv-credit">
      <template v-if="usingFallback">{{ t("patient.credit_fallback") }}</template>
      <template v-else>
        {{ t("patient.credit_model") }} «<a href="https://poly.pizza/m/eWGDnQ0jzmH" target="_blank" rel="noopener noreferrer">Male base</a>» · Артур Мигранов ·
        <a href="https://creativecommons.org/licenses/by/3.0/" target="_blank" rel="noopener noreferrer">CC BY</a>
      </template>
    </p>
  </div>
</template>
