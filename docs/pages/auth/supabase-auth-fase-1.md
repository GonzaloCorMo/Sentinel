# Auth Fase 1 con Supabase

## Implementacion actual (Vue 3)

La aplicacion de producto es **Vue 3 + Vite** en `frontend/`:

- Pantalla de login: `frontend/src/views/LoginView.vue` — ruta `/login`, estilo HPE CDS.
- Cliente Supabase: `frontend/src/lib/supabase.ts` — PKCE, `detectSessionInUrl: false`; el canje de codigo OAuth/magic link esta en `frontend/src/views/AuthCallbackView.vue` (`/auth/callback`).
- Rutas protegidas: `frontend/src/router/index.ts` — sin sesion, las rutas con `requiresAuth` redirigen a `/login?next=…`.

Si faltan `VITE_SUPABASE_URL` y `VITE_SUPABASE_ANON_KEY` en `frontend/.env`, la UI se monta y muestra un aviso (no pantalla en blanco).

## Objetivo

Implementar autenticacion en local:

- email + contrasena (login y registro con confirmacion de contrasena)
- **Google OAuth** (boton "Continuar con Google" en login)
- **magic link** (OTP por correo, boton "Enviar magic link" en login)
- recuperar contrasena por correo y **pantalla dedicada** para la nueva clave

Las sesiones son las de Supabase (JWT + cookies `sb-*`).

## Piezas implementadas (Vue)

- `frontend/src/views/LoginView.vue` — formulario de login con email/password, Google OAuth y magic link
- `frontend/src/views/AuthCallbackView.vue` — canje PKCE / hash en cliente
- `frontend/src/lib/supabase.ts` — singleton del cliente Supabase con `detectSessionInUrl: false`
- `frontend/src/router/index.ts` — guard de autenticacion con `requiresAuth`
- `supabase/config.toml` — configuracion local de Supabase

## Flujo de login estandar

1. Usuario introduce email y contrasena en `/login`.
2. Se llama a `supabase.auth.signInWithPassword()`.
3. Exito → redireccion a `/map` (o la ruta en `?next=`).

## Flujo Google OAuth

1. Click en "Continuar con Google" → `supabase.auth.signInWithOAuth({ provider: 'google' })`.
2. Redireccion a Google → autorizacion → callback a `/auth/callback`.
3. Canje PKCE → sesion activa → redireccion a `/map`.

## Flujo recuperacion de contrasena

1. En `/login`: correo + "Olvidaste tu contrasena?" → `resetPasswordForEmail` con `redirectTo` a la URL de reset.
2. Correo (Inbucket local: `http://127.0.0.1:54324`) → enlace → canje de codigo.
3. Formulario "Establece tu nueva contrasena" (dos campos + guardar).
4. Tras guardar → cierre de sesion de recuperacion → `/login?message=password_updated`.

## Variables clave

| Variable | Archivo |
|----------|---------|
| `VITE_SUPABASE_URL` | `frontend/.env` |
| `VITE_SUPABASE_ANON_KEY` | `frontend/.env` |

## Redirect URLs permitidas en local

Incluye `http://localhost:5173/auth/callback` y variantes con `127.0.0.1`.

En `supabase/config.toml`, **`site_url`** debe coincidir con como entras en el navegador (ej. `http://localhost:5173`).

## Validacion manual

1. Registro con email y contrasena (con confirmacion).
2. Login con email/password.
3. Login con Google OAuth.
4. "Olvidaste tu contrasena?" → correo → enlace → pantalla reset → nueva clave → login.
