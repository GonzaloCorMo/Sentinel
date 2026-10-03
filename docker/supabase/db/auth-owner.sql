-- Reasigna el ownership del schema `auth` y todos sus objetos a
-- `supabase_auth_admin`. Sin esto, GoTrue falla al arrancar al intentar
-- `CREATE OR REPLACE FUNCTION auth.uid()` (que fue creada por supabase_admin).
DO $$
DECLARE
  rec record;
BEGIN
  ALTER SCHEMA auth OWNER TO supabase_auth_admin;

  FOR rec IN
    SELECT format(
      'ALTER FUNCTION auth.%I(%s) OWNER TO supabase_auth_admin',
      p.proname,
      pg_catalog.pg_get_function_identity_arguments(p.oid)
    ) AS cmd
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'auth'
  LOOP
    EXECUTE rec.cmd;
  END LOOP;

  FOR rec IN
    SELECT format('ALTER TABLE auth.%I OWNER TO supabase_auth_admin', c.relname) AS cmd
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'auth' AND c.relkind IN ('r', 'p')
  LOOP
    EXECUTE rec.cmd;
  END LOOP;

  FOR rec IN
    SELECT format('ALTER SEQUENCE auth.%I OWNER TO supabase_auth_admin', c.relname) AS cmd
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'auth' AND c.relkind = 'S'
  LOOP
    EXECUTE rec.cmd;
  END LOOP;
END $$;
