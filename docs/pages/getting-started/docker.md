# Arranque con Docker

Todo el proyecto corre como un único stack de Docker Compose. No hace falta instalar Node, Python ni Supabase CLI en la máquina: solo Docker.

El **chat LLM** se delega a un endpoint vLLM externo (Gemma flash + Qwen flagship, API OpenAI-compat); no se descarga ningún modelo de chat al host. **Embeddings** (RAG / pgvector) sí corren en local vía Ollama con `nomic-embed-text`.

## Despliegue de referencia

- **Servidor**: `10.10.48.25`; todos los puertos siguientes están publicados ahí.
- **Endpoint vLLM externo**: `10.10.48.10:8000` (flash) · `10.10.48.10:8001` (flagship). Configurable con `LLM_FLASH_BASE_URL` / `LLM_FLAGSHIP_BASE_URL`.
- **Eventos externos**: no requieren infraestructura; el backend incluye un generador mock y endpoints de ingesta REST. Ver [Fuente de eventos](../technical/fuente-de-eventos.md).

## Requisitos

- **Docker Engine ≥ 20.10** y el **plugin `docker compose`**.
  - Ubuntu/Debian: `sudo apt install docker-compose-plugin`
  - Fedora: `sudo dnf install docker-compose-plugin`
  - Mac/Windows: incluido en Docker Desktop.
- **~7 GB libres** (imágenes + 4 grafos OSRM + modelo embeddings).
- **GPU del host (opcional, recomendado para embeddings)**:
  - NVIDIA → driver + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
  - AMD (MI300X / MI250 / RX 7000) → kernel con módulo `amdgpu` + acceso a `/dev/kfd` y `/dev/dri`. El contenedor `ollama/ollama:rocm` trae ROCm dentro.
  - Sin GPU → perfil `cpu` (más lento, válido para desarrollo).
- Conectividad con el endpoint vLLM externo (`10.10.48.10:8000/8001` por defecto) para el chat.

Para que tu usuario pueda ejecutar `docker` sin `sudo`:
```bash
sudo usermod -aG docker $USER     # re-login después
```

## Arranque

```bash
# 1. Clonar
git clone <url-del-repo>
cd <directorio-del-repo>

# 2. Configurar .env
cp .env.example .env
python3 scripts/generate-supabase-keys.py   # imprime secretos frescos
# → copia el output dentro de .env (sustituye los valores REPLACE_WITH_…)

# 3. Levantar todo (elige perfil GPU del host para Ollama embeddings)
docker compose --profile gpu-nvidia up -d   # CUDA
docker compose --profile gpu-amd    up -d   # ROCm (MI300X / MI250)
docker compose --profile cpu        up -d   # sin GPU

# 4. Ver logs
docker compose logs -f
```

La **primera vez** tarda ~10 min:

- Descarga de imágenes Docker (~2 GB; tag `:rocm` añade ~3 GB extra solo si usas el perfil AMD).
- Descarga de los 4 extractos OSM y compilación de los grafos OSRM (Aruba/Madrid/Bogotá/CDMX). Cada par `osrm-fetcher-<region>` + `osrm-builder-<region>` se ejecuta una sola vez y sale.
- Descarga del modelo de embeddings `nomic-embed-text` (~270 MB) por `ollama-init`.
- El chat LLM **no** descarga nada: se sirve desde el endpoint vLLM externo.

A partir de la segunda vez, todo arranca en <1 min (volúmenes persistidos).

## Puertos expuestos

| Servicio | URL local | URL producción | Puerto |
|---|---|---|---|
| Frontend (Vue) | http://localhost:5173 | http://10.10.48.25:5173 | 5173 |
| API simulación (FastAPI) | http://localhost:8080 | http://10.10.48.25:8080 | 8080 |
| OpenAPI YAML | `/openapi.yaml` | `/openapi.yaml` | 8080 |
| Documentación (VitePress) | http://localhost:3001 | http://10.10.48.25:3001 | 3001 |
| Supabase API (Kong) | http://localhost:54321 | http://10.10.48.25:54321 | 54321 |
| Supabase Studio | http://localhost:54323 | http://10.10.48.25:54323 | 54323 |
| Postgres | `localhost:54322` | `10.10.48.25:54322` | 54322 |
| OSRM Aruba (default) | http://localhost:5003 | http://10.10.48.25:5003 | 5003 |
| OSRM Madrid | http://localhost:5000 | http://10.10.48.25:5000 | 5000 |
| OSRM Bogotá | http://localhost:5001 | http://10.10.48.25:5001 | 5001 |
| OSRM CDMX | http://localhost:5002 | http://10.10.48.25:5002 | 5002 |
| Mosquitto (MQTT) | `localhost:1883` | `10.10.48.25:1883` | 1883 |
| Ollama (embeddings, red interna) | `http://ollama:11434` | n/a | — |
| vLLM externo flash (chat) | n/a | `http://10.10.48.10:8000/v1` | 8000 |
| vLLM externo flagship (chat) | n/a | `http://10.10.48.10:8001/v1` | 8001 |

Abre: http://10.10.48.25:5173/login (o `http://localhost:5173/login` en local).

## Operaciones habituales

```bash
# Parar (conserva datos)
docker compose down

# Parar Y borrar DB + grafos OSRM + modelo embeddings Ollama
docker compose down -v
rm -rf docker/osrm-data/*/region.osrm* docker/osrm-data/*/map.osm.pbf

# Reconstruir imágenes
./build.sh                       # todas, incremental
./build.sh simulation frontend   # solo indicadas
./build.sh --no-cache            # rebuild completo
./build.sh --pull                # refresca imágenes base
./build.sh --parallel            # paralelo

# Logs de un servicio
docker compose logs -f simulation
docker compose logs -f frontend

# Shell dentro de un contenedor
docker compose exec simulation bash
docker compose exec supabase-db psql -U postgres

# Forzar reseed de protocolos
docker compose exec simulation python -c "from app.knowledge_seeder import seed_knowledge_force; seed_knowledge_force()"

# Probar API simulación
curl http://localhost:8080/health
curl http://localhost:8080/api/sim/state | jq
```

### build.sh — script de construcción

`build.sh` envuelve `docker compose build` con validación y reporte:

- Sin argumentos construye `simulation`, `frontend`, `ml-service`, `docs`.
- Valida que cada imagen existe al terminar (detecta fallos silenciosos).
- Muestra tiempo total, tamaño y fecha de cada imagen.
- Ante fallo imprime causas comunes (TLS MITM, timeout, espacio en disco).

Flags:

| Flag | Uso |
|---|---|
| `--no-cache` | Ignora cache de capas, rebuild desde cero |
| `--pull` | Refresca imágenes base (python, node) antes del build |
| `--parallel` | Construye servicios en paralelo (logs entrelazados) |
| `-h`, `--help` | Muestra ayuda |

Flujo típico tras tocar Dockerfile o `requirements.txt`:

```bash
./build.sh simulation && ./up.sh
```

## Desarrollo (hot-reload)

- **Frontend**: `frontend/src/`, `public/`, `index.html` y `vite.config.ts` están montados como volúmenes. Al guardar un cambio, Vite hace HMR automáticamente.
- **Simulación**: `simulation/app/` está montado como volumen, así que tras cambiar Python basta con `docker compose restart simulation` (sin rebuild). Para hot-reload puro edita `simulation/Dockerfile` añadiendo `--reload` al CMD de uvicorn.

## Troubleshooting

**`osrm-builder-<region>` falla con "out of memory"**
Los extractos por defecto son ligeros (Aruba ~3 MB, Madrid/Bogotá/CDMX ~30-100 MB). Si cambias a un PBF mayor (`OSRM_PBF_URL_<REGION>` en `.env`), considera dar más memoria a Docker Desktop.

**Supabase Studio pide login y no entra**
Usuario y password están en tu `.env` (`DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`).

**El frontend dice "Missing Supabase URL"**
Falta `.env` en la raíz. No basta con `.env.example`; hay que copiarlo y rellenarlo.

**Las llamadas a `/api/...` dan 502**
El servicio `simulation` no ha arrancado. Mira `docker compose logs simulation`. Suele ser por migraciones fallidas en `supabase-db`.

**No aparecen eventos externos ni meteorología en el mapa**
Comprueba que `EVENT_SOURCE=mock` (valor por defecto) y revisa el estado de la fuente con `GET /api/events/status`. Con `EVENT_SOURCE=off` solo llegan datos por `POST /api/events/ingest` y `POST /api/weather/ingest`.

**`docker compose build` falla con error TLS / certificado inválido**
Mensaje tipo `tls: failed to verify certificate` apuntando a `docker-images-prod.*.r2.cloudflarestorage.com`. Significa que la red local intercepta TLS (proxy corporativo, firewall educativo, filtro ISP). Comprobar con:
```bash
echo | openssl s_client -servername docker-images-prod.6aa30f8b08e16409b46e0173d6de2f56.r2.cloudflarestorage.com \
  -connect docker-images-prod.6aa30f8b08e16409b46e0173d6de2f56.r2.cloudflarestorage.com:443 2>/dev/null \
  | openssl x509 -noout -subject -issuer
```
Si el subject/issuer no es Cloudflare, hay MITM. Soluciones:

1. Cambiar de red (tethering móvil, VPN).
2. Añadir el CA del interceptor al trust de Docker (solo si la red es autorizada).
3. Precargar imágenes desde otra máquina:
   ```bash
   # en máquina con red limpia
   docker pull python:3.12-slim && docker save python:3.12-slim -o py312.tar
   # en esta máquina
   docker load -i py312.tar
   ```

**Quiero resetear la BD pero conservar los grafos OSRM**
```bash
docker compose down
docker volume ls | grep supabase-db-data   # el prefijo es el nombre del proyecto compose
docker volume rm <proyecto>_supabase-db-data
docker compose up -d
```

## Cómo comparte datos el equipo

Cada PC tiene su propia DB local (el volumen `supabase-db-data` no se comparte). Para sincronizar:

- **Migraciones SQL**: commiteadas en `supabase/migrations/`. Se aplican al crear la DB por primera vez.
- **Datos de prueba (semilla)**: el simulador hace seed del RAG automáticamente si `protocols_knowledge` está vacía.
- **Tipos de flota**: `fleet_entity_types` se siembra al arrancar (powertrain combustion/electric/unique con desc/caps generadas por LLM).
- **Escenarios guardados**: tabla `saved_scenarios`. Para compartir, exporta con `pg_dump` y pásalo por git.
