# syntax=docker/dockerfile:1

# ── deps: instala dependencias con lockfile reproducible ─────────────
FROM node:22-alpine AS deps
# lastUpdated de VitePress invoca `git`: sin el binario cada página da 404.
RUN apk add --no-cache git
WORKDIR /docs
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# ── build: genera el sitio estático (docker build --target build) ────
FROM deps AS build
COPY . .
RUN npm run build

# ── dev (por defecto): servidor VitePress con HMR ────────────────────
# docker-compose monta ./docs/pages y ./docs/.vitepress como volúmenes.
FROM node:22-alpine AS dev
RUN apk add --no-cache git
WORKDIR /docs
ENV NODE_ENV=development
COPY --from=deps /docs/node_modules ./node_modules
COPY . .
EXPOSE 3001
CMD ["npx", "vitepress", "dev", "--host", "0.0.0.0", "--port", "3001"]
