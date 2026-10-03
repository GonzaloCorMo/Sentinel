# Sentinel — documentación

Sitio de documentación técnica y operativa de Sentinel, construido con [VitePress](https://vitepress.dev/).

## Estructura

```
docs/
├── .vitepress/
│   ├── config.mts        # título, sidebar, búsqueda local, opciones del sitio
│   └── theme/            # tema: DefaultTheme + custom.css
├── pages/                # contenido Markdown (srcDir)
│   ├── index.md          # portada (layout home)
│   ├── getting-started/  auth/  arquitectura/
│   ├── technical/  ml/  guia-usuario/
├── Dockerfile
└── package.json
```

Para añadir una página, crea el `.md` en `pages/` y enlázala en el `sidebar` de `.vitepress/config.mts`. Los enlaces internos se escriben relativos con extensión `.md` (p. ej. `[API](../technical/simulation-api-http-sse.md)`); el build falla si hay enlaces muertos.

Bloques destacados: `::: info`, `::: tip`, `::: warning`, `::: danger` y `::: details` (cerrados con `:::`).

## Desarrollo local

Requiere Node.js 20 o superior.

```bash
cd docs
npm install
npm run dev        # servidor con recarga en caliente
```

## Build estático

```bash
npm run build      # genera .vitepress/dist
npm run preview    # sirve el build en http://localhost:3001
```

## Docker

Con el stack completo (`./up.sh` o `docker compose up -d`) la documentación se sirve en `http://localhost:3001`. El servicio monta `pages/` y `.vitepress/` como volúmenes, así que los cambios se reflejan sin reconstruir la imagen.

Imagen aislada:

```bash
docker build -t sentinel-docs ./docs
docker run --rm -p 3001:3001 sentinel-docs
```

Para obtener solo el sitio estático: `docker build --target build -t sentinel-docs-build ./docs` (el resultado queda en `/docs/.vitepress/dist`).
