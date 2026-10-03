# Levantar el proyecto

La forma recomendada es **Docker Compose**. El único requisito es tener Docker + el plugin `docker compose` instalados y el repositorio clonado.

## Arranque rápido (una sola orden)

```bash
cp .env.example .env
python3 scripts/generate-supabase-keys.py   # pega el output en .env
./up.sh
```

Al terminar muestra un banner con todas las URLs. La primera vez tarda ~10–15 min (descarga de imágenes, grafos OSRM y modelo de embeddings); los siguientes arranques, segundos.

Guía detallada: [Arranque con Docker](docker.md).

## Rebuild de imágenes

Tras tocar un `Dockerfile`, `requirements.txt` o `package.json`:

```bash
./build.sh                       # todas, incremental
./build.sh simulation            # solo una
./build.sh --no-cache            # rebuild completo
./build.sh --pull --parallel     # refresca bases y construye en paralelo
./build.sh && ./up.sh            # reconstruir y levantar
```

Detalle completo + troubleshooting (incluido error TLS por MITM de red): [Arranque con Docker → Operaciones habituales](docker.md#operaciones-habituales).

## Dev nativo (sin Docker)

No recomendado para operación normal. Si necesitas iterar muy rápido sobre frontend o backend:

### Frontend

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173
```

Exige tener el backend (`simulation`) y Supabase en marcha vía Docker. El servidor de Vite lee `VITE_SUPABASE_URL`, `DEV_PROXY_API_TARGET` y `DEV_PROXY_SB_TARGET` del entorno.

### Backend

```bash
cd simulation
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Requiere Postgres + pgvector + OSRM + Ollama accesibles. Lo más cómodo: levantar stack completo (`./up.sh`) y ejecutar solo el backend nativo aparte (detener el container `simulation` con `docker compose stop simulation`).

### Documentación

```bash
cd docs
npm install
npm run dev
```

Ver `docs/README.md` para más opciones (build estático, Docker).

## Variables de entorno

- Único `.env` en la raíz. Plantilla: `.env.example`.
- Docker Compose inyecta cada variable al servicio pertinente.
- No hay `.env` en subdirectorios.
