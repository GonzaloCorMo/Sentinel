-- Publica el JWT secret vía app.settings para que funciones Postgres-side
-- (RLS con auth.jwt(), triggers, etc.) puedan firmar/verificar igual que GoTrue.
\set jwt_secret `echo "$JWT_SECRET"`
\set jwt_exp    `echo "${JWT_EXP:-3600}"`

ALTER DATABASE postgres SET "app.settings.jwt_secret" TO :'jwt_secret';
ALTER DATABASE postgres SET "app.settings.jwt_exp"    TO :'jwt_exp';
