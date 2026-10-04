# Auth con Supabase (fase 1)

## Implementación actual

La aplicación de producto es **Vue 3 + Vite** en `frontend/`:

- Pantalla de login: `frontend/src/views/LoginView.vue` (ruta `/login`).
- Cliente Supabase: `frontend/src/lib/supabase.ts`, con PKCE y `detectSessionInUrl: false`. El canje del código OAuth / magic link se hace en `frontend/src/views/AuthCallbackView.vue` (`/auth/callback`).
- Nueva contraseña tras recuperación: `frontend/src/views/AuthUpdatePasswordView.vue` (`/auth/update-password`).
- Rutas protegidas: `frontend/src/router/index.ts`. Sin sesión, las rutas con `requiresAuth` redirigen a `/login?next=…`.

Si faltan `VITE_SUPABASE_URL` y `VITE_SUPABASE_ANON_KEY`, la UI se monta igualmente y muestra un aviso (no una pantalla en blanco).

## Alcance

- Email + contraseña (login y registro con confirmación de contraseña).
- **Google OAuth** (botón «Continuar con Google»).
- **Magic link** (OTP por correo).
- Recuperación de contraseña por correo con **pantalla dedicada** para la nueva clave.

Las sesiones son las de Supabase (JWT + cookies `sb-*`).

## Roles y redirección

| Rol | Destino tras login |
|---|---|
| `admin` (operador) | `/map` (o la ruta indicada en `?next=`) |
| `vehicle` (piloto) | `/vehicle` |
| `citizen` | `/message-alert` |

## Flujo de login estándar

1. El usuario introduce email y contraseña en `/login`.
2. Se llama a `supabase.auth.signInWithPassword()`.
3. Si tiene éxito, redirige según el rol (o a `?next=`).

## Flujo Google OAuth

1. «Continuar con Google» → `supabase.auth.signInWithOAuth({ provider: 'google' })`.
2. Redirección a Google → autorización → callback a `/auth/callback`.
3. Canje PKCE → sesión activa → redirección.

## Flujo de recuperación de contraseña

1. En `/login`: correo + «¿Olvidaste tu contraseña?» → `resetPasswordForEmail` con `redirectTo` a la URL de reset.
2. Correo (Inbucket en local: `http://127.0.0.1:54324`) → enlace → canje del código.
3. Formulario «Establece tu nueva contraseña» (dos campos + guardar).
4. Tras guardar se cierra la sesión de recuperación → `/login?message=password_updated`.

## Variables

| Variable | Uso |
|----------|-----|
| `VITE_SUPABASE_URL` | URL pública de Supabase (en navegador se usa el proxy `/sb` del propio origen). |
| `VITE_SUPABASE_ANON_KEY` | Clave anónima. |

Ambas se definen en el `.env` de la raíz y Docker Compose las inyecta en el servicio `frontend`.

## Redirect URLs permitidas en local

Incluye `http://localhost:5173/auth/callback` y sus variantes con `127.0.0.1`. La variable **`SITE_URL`** de `.env` (que llega a GoTrue como `GOTRUE_SITE_URL`) debe coincidir con la URL desde la que entras en el navegador (p. ej. `http://localhost:5173`).

## Validación manual

1. Registro con email y contraseña (con confirmación).
2. Login con email y contraseña.
3. Login con Google OAuth.
4. «¿Olvidaste tu contraseña?» → correo → enlace → pantalla de reset → nueva clave → login.
