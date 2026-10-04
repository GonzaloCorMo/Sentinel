# Arranque con Docker

Todo el proyecto corre como un único stack de Docker Compose. No hace falta instalar Node, Python ni Supabase CLI en la máquina: solo Docker.

La **IA es 100 % local**: el chat, los comandos, los informes de turno y el razonamiento de la IA observadora corren en **Ollama** dentro del propio stack (modelo de chat `qwen2.5:3b` por defecto y embeddings `nomic-embed-text`). No hace falta ningún servicio externo de IA. Detalle en [IA local con Ollama](../technical/ai-chatbot-rag.md#ia-local-con-ollama).

## Qué incluye el stack

- **Todo en `localhost`**: los puertos de la tabla de abajo se publican en tu máquina.
- **Ollama**: un contenedor por perfil (`ollama-nvidia`, `ollama-amd` u `ollama-cpu`), accesible en la red interna como `ollama`. Si prefieres otro servidor compatible con OpenAI (vLLM, LM Studio…), cambia `LLM_FLASH_BASE_URL` / `LLM_FLAGSHIP_BASE_URL` en `.env`.
- **Eventos externos**: no requieren infraestructura; el backend incluye un generador mock y endpoints de ingesta REST. Ver [Fuente de eventos](../technical/fuente-de-eventos.md).

## Requisitos

- **Docker Engine ≥ 20.10** y el **plugin `docker compose`**.
  - Ubuntu/Debian: `sudo apt install docker-compose-plugin`
  - Fedora: `sudo dnf install docker-compose-plugin`
  - Mac/Windows: incluido en Docker Desktop.
- **~9 GB libres** (imágenes + 3 grafos OSRM + modelos de Ollama, ~2,2 GB).
- **GPU del host (opcional, muy recomendada para el chat)**:
  - NVIDIA → driver + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
  - AMD (MI300X / MI250 / RX 7000) → kernel con módulo `amdgpu` + acceso a `/dev/kfd` y `/dev/dri`. El contenedor `ollama/ollama:rocm` trae ROCm dentro.
  - Sin GPU → perfil `cpu` (funciona, pero las respuestas del chat tardan bastante más).

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

# 3. Levantar todo (elige el perfil según la GPU del host; lo usa Ollama)
docker compose --profile gpu-nvidia up -d   # CUDA
docker compose --profile gpu-amd    up -d   # ROCm (MI300X / MI250)
docker compose --profile cpu        up -d   # sin GPU

# 4. Ver logs
docker compose logs -f
```

La **primera vez** tarda ~10 min:

- Descarga de imágenes Docker (~2 GB; tag `:rocm` añade ~3 GB extra solo si usas el perfil AMD).
- Descarga del extracto OSM de Galicia, recorte a Santiago y compilación del grafo OSRM. `osrm-fetcher-santiago` y `osrm-builder-santiago` se ejecutan una sola vez y salen.
- Descarga de los modelos de Ollama por `ollama-init`: chat `qwen2.5:3b` (~1,9 GB) y embeddings `nomic-embed-text` (~270 MB). Mientras no termine, el chat no responde.

A partir de la segunda vez, todo arranca en <1 min (volúmenes persistidos).

## Puertos expuestos

| Servicio | URL local | Puerto |
|---|---|---|
| Frontend (Vue) | http://localhost:5173 | 5173 |
| API simulación (FastAPI) | http://localhost:8080 | 8080 |
| OpenAPI YAML | http://localhost:8080/openapi.yaml | 8080 |
| Documentación (VitePress) | http://localhost:3001 | 3001 |
| Supabase API (Kong) | http://localhost:54321 | 54321 |
| Supabase Studio | http://localhost:54323 | 54323 |
| Postgres | `localhost:54322` | 54322 |
| OSRM Santiago de Compostela | http://localhost:5000 | 5000 |
| Mosquitto (MQTT) | `localhost:1883` | 1883 |
| Ollama (chat + embeddings, red interna) | `http://ollama:11434` | — |

Abre: http://localhost:5173/login.

## Operaciones habituales

```bash
# Parar (conserva datos)
docker compose down

# Parar Y borrar DB + grafos OSRM + modelos de Ollama
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

**`osrm-builder-santiago` falla con "out of memory"**
El extracto por defecto es ligero: Santiago se recorta del extracto de Galicia con osmium (~110 MB de descarga y un grafo pequeño). Si amplías el recorte (`OSRM_BBOX_SANTIAGO`) o cambias de extracto (`OSRM_PBF_URL_SANTIAGO`), considera dar más memoria a Docker Desktop.

**El chat tarda mucho o no responde**
Comprueba que `ollama-init` terminó (`docker compose logs ollama-init` debe acabar en `OK`) y que usas el perfil de GPU adecuado. Con el perfil `cpu` las respuestas pueden tardar bastante. Ver [IA local con Ollama](../technical/ai-chatbot-rag.md#ia-local-con-ollama).

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

- **Migraciones SQL**: commiteadas en `supabase/migrations/`. `supabase-migrator` aplica las que falten en cada arranque (por nombre, en `public._migrations`).
- **Datos de prueba (semilla)**: el simulador hace seed del RAG automáticamente si `protocols_knowledge` está vacía.
- **Tipos de flota**: `fleet_entity_types` se siembra al arrancar (powertrain combustion/electric/unique con desc/caps generadas por LLM).
