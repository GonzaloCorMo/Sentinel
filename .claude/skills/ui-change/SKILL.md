---
name: ui-change
description: Checklist para crear o modificar pantallas, componentes o textos del frontend de Sentinel con su sistema visual (neutros, radios ≤4px, color solo semántico), i18n en es/en/gl, lenguaje claro sin jerga y revisión en ambos temas. Úsalo en cualquier cambio visible para el usuario.
---

# Cambios de interfaz

Lee antes `frontend/CLAUDE.md`. Después:

1. **Contenido primero**: ¿qué necesita saber o hacer el operador en esta pantalla? Escribe el título y la línea que explica para qué sirve la página. Quita lo que no ayude a decidir.
2. **Textos**: todos con `t()`. Añade la clave en `es`, `en` y `gl` a la vez, con un tono claro y operativo: «Hacia la emergencia», no `RESPONDING`; «Canal principal», no `mqtt_active`. Nada de jerga técnica, emojis decorativos ni frases de relleno. Traduce siempre los valores que vienen del backend (ver `lib/unitStatus.ts` y las secciones `events.*`, `map.*` y `emergency_type.*` de los locales).
3. **Estilo**: usa las clases existentes:
   - neutros `slate-*`, separadores `border-slate-800`, radios `rounded` / `rounded-sm`;
   - botón primario `bg-slate-100 text-slate-950`; cifras en `font-mono`;
   - color vivo (`green`/`amber`/`red`) solo si comunica un estado o una alerta;
   - nada de degradados, glows, `rounded-xl/2xl` ni `transition-all`.
4. **Datos externos en HTML**: escápalos (`esc()`) o pásalos por `sanitizeHtml`/`sanitizeSvg`.
5. **Accesibilidad**: los botones de solo icono llevan `aria-label` y `title`; los campos, `label`; el contraste debe ser suficiente en ambos temas.
6. **Comprobar**:
   - `bash scripts/verify.sh frontend` (typecheck, build y claves i18n alineadas);
   - después, revisión visual en el navegador en tema oscuro **y** claro, a anchura de escritorio y estrecha, con la consola sin errores.
