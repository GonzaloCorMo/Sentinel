---
name: verify
description: Verifica que un cambio en Sentinel está realmente terminado — comprobaciones estáticas (typecheck, build, i18n, pyflakes, docs, compose), pruebas de humo contra el stack y revisión visual. Úsalo antes de decir que algo funciona o antes de hacer commit.
---

# Verificar un cambio

1. **Estático** (siempre):
   ```bash
   bash scripts/verify.sh            # o solo la parte tocada: frontend | backend | docs | compose
   ```
   Si falla, arregla la causa; no la silencies (nada de `// @ts-ignore`, `# noqa` ni quitar comprobaciones del script).

2. **En ejecución** (si cambió el backend, la API, Docker o el flujo de datos):
   ```bash
   docker compose ps                 # todo "running"/"healthy"; los fetch/builder/migrator, "exited (0)"
   docker compose restart simulation # si tocaste Python
   bash scripts/smoke.sh             # añade --ai si tocaste chat, órdenes o informes
   ```
   Tras reiniciar `simulation`, el estado está vacío: genera un escenario para probar flujos de despacho.

3. **Visual** (si cambió la UI): carga las tools de Claude in Chrome, abre `http://localhost:5173`, revisa la vista afectada en **tema oscuro y claro** y lee la consola (`read_console_messages`, solo errores). Inicia sesión con `admin@sentinel.local` (o `vehiculo@sentinel.local` para `/vehicle`); la contraseña está en `.env`. No la repitas en el chat.

4. **Informe**: di qué comprobaste y con qué resultado, y qué quedó sin comprobar. Si algo falla y no es tuyo, dilo; no lo escondas.
