# Probar la PWA en móvil

Guía paso a paso para probar la app ciudadana (`/m`) desde un móvil Android en la misma red WiFi que el PC de desarrollo.

::: info Arquitectura en dev
El frontend (Vite, puerto 5173) actúa de único punto de entrada. Las llamadas a `/api/*` van al motor FastAPI y las de `/sb/*` van a Supabase (Kong) vía proxy interno. El móvil **solo necesita el puerto 5173** alcanzable.
:::


## Requisitos

- Stack Docker corriendo (`./up.sh`).
- PC y móvil en **la misma red WiFi**.
- Chrome Android (recomendado — mejor soporte de Web Speech + Geolocation). Safari iOS también sirve.

## Problema: *"Ubicación: Only secure origins are allowed"*

Chrome bloquea `navigator.geolocation` y `SpeechRecognition` en orígenes **no seguros**, salvo `localhost`. Desde móvil accedes por IP (`http://192.168.x.x:5173`) → HTTP = inseguro → geo/voz bloqueados.

**Tres soluciones.** Elige una.


## Opción A — Flag de Chrome (30s, ideal para probar rápido)

1. En el **móvil**, Chrome → barra de dirección → `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
2. En el textarea añade (sin comillas): `http://192.168.1.42:5173`
   (sustituye por tu IP real; varios separados por coma)
3. Pon el flag en **Enabled**
4. Botón **Relaunch**

Ese origen se trata como seguro → geo + voz funcionan.

**Solo dev.** No lo uses para demos públicas.


## Opción B — ngrok (un único túnel HTTPS, más limpio)

El proxy `/sb` permite que un único túnel al puerto 5173 cubra también Supabase.

```bash
# Instalar ngrok una vez
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok
ngrok config add-authtoken <TOKEN_DE_NGROK_COM>

# Tunelizar
ngrok http 5173
```

Imprime algo como `https://abc123.ngrok-free.app`. Abre esa URL en el móvil → HTTPS → geo + voz sin flags. El ancho de banda del plan gratis sobra para demos.


## Opción C — Vite HTTPS con `mkcert` (self-signed permanente)

Para demos en la LAN sin ngrok y con HTTPS real:

```bash
sudo apt install libnss3-tools
curl -JLO "https://dl.filippo.io/mkcert/latest?for=linux/amd64"
chmod +x mkcert-v*-linux-amd64 && sudo mv mkcert-v*-linux-amd64 /usr/local/bin/mkcert
mkcert -install
cd frontend
mkcert 192.168.1.42 localhost 127.0.0.1   # sustituye IP
```

Genera dos ficheros `*.pem`. Añade a `vite.config.ts`:

```ts
import fs from "node:fs";
server: {
  port: 5173,
  host: "0.0.0.0",
  https: {
    key: fs.readFileSync("./192.168.1.42+2-key.pem"),
    cert: fs.readFileSync("./192.168.1.42+2.pem"),
  },
  // ... resto igual
}
```

Y reinicia:
```bash
docker compose restart frontend
```

En el móvil la 1ª vez verás un warning de certificado — instala el CA root de mkcert copiándolo al teléfono (está en `$(mkcert -CAROOT)/rootCA.pem`) y trust en Ajustes → Seguridad.


## Flujo de prueba (tras elegir A, B o C)

### 1. Averigua la IP del PC (solo opción A)

```bash
ip -4 addr | grep -oP 'inet \K[\d.]+' | grep -v 127.0.0.1
```

### 2. Abre el firewall (una sola vez, opciones A y C)

```bash
# Ubuntu/Debian
sudo ufw allow from 192.168.0.0/16 to any port 5173 proto tcp

# Fedora/RHEL
sudo firewall-cmd --add-port=5173/tcp
```

(opción B no necesita firewall — el túnel va desde el PC hacia fuera)

### 3. Probar conectividad

Abre en el móvil:
- **Opción A**: `http://192.168.1.42:5173/m`
- **Opción B**: `https://abc123.ngrok-free.app/m`
- **Opción C**: `https://192.168.1.42:5173/m`

Deberías ver la pantalla SOS.

### 4. Flujo completo

1. `/login` → **crear cuenta** con email + contraseña cumpliendo los 4 requisitos (mayús/minús/dígito/≥8)
2. Tras login te redirige al dashboard (la pantalla desktop no cabe bien en móvil; es normal — hay una vista ciudadana específica)
3. Abre `/m` → botón SOS rojo
4. Acepta permisos de **ubicación** y **micrófono** al pedirlos
5. Di algo como *"Accidente de coche en la rotonda, dos heridos"*
6. Pulsa "He terminado"
7. Revisa transcript editable + tipo + mapa preview
8. "Enviar emergencia" → toast verde + ID
9. En el PC abre el dashboard → la incidencia aparece en el mapa + motor asigna ambulancia

### 5. Instalar como app (PWA)

Chrome Android con `/m` abierta → menú **⋮** → **Añadir a pantalla de inicio**. Se instala con el icono de la app apuntando directo a `/m` y sin barra de navegador.


## Troubleshooting

| Síntoma | Causa | Fix |
|---|---|---|
| `Ubicación: Only secure origins are allowed` | HTTP desde IP no-localhost | Opción A, B o C (arriba) |
| `Failed to fetch` en login/signup | Origen ≠ 5173 no llega a Supabase | El proxy `/sb` ya está puesto — si aún falla, `docker compose up -d frontend` para aplicar cambios de `vite.config.ts` |
| `SpeechRecognition not supported` | Firefox Android o similar | Usa Chrome o Edge |
| Voz graba pero no transcribe | Web Speech requiere internet (usa servidores Google del navegador) | Conecta datos/WiFi con salida |
| Página no carga desde la IP | Firewall | Paso 2 de esta guía |
| `NET::ERR_CERT_AUTHORITY_INVALID` (opción C) | Móvil no tiene el root CA instalado | Copia `rootCA.pem` de `mkcert -CAROOT` al móvil y márcalo como CA de confianza |
| Login OK pero dashboard se ve mal en móvil | Dashboard asume desktop | Es normal — la vista móvil es `/m`. Rol vehículo con UI móvil queda para fase 2 |

## Detalles técnicos

- `vite.config.ts` define 2 proxies: `/api` → simulation, `/sb` → supabase-kong (con rewrite que elimina `/sb`).
- `src/lib/supabase.ts` usa `window.location.origin + "/sb"` como base URL → mismo origen que el frontend → cero CORS / mixed-content / Supabase-URL-mal-configurada.
- El `.env` sigue con `VITE_SUPABASE_URL` (se ignora en browser, se usa solo como fallback para SSR/tests).
