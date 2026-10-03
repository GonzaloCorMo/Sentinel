-- Normaliza los passwords de los roles internos de Supabase al POSTGRES_PASSWORD.
-- La imagen supabase/postgres crea estos roles en su bootstrap con passwords
-- aleatorios; GoTrue / PostgREST / Storage esperan poder autenticar con el
-- mismo que configuramos para `postgres`.
\set pgpass `echo "$POSTGRES_PASSWORD"`
SELECT set_config('app.pgpass', :'pgpass', false);

DO $$
DECLARE
  role_name text;
  pw text := current_setting('app.pgpass');
  roles text[] := ARRAY[
    'authenticator',
    'pgbouncer',
    'supabase_auth_admin',
    'supabase_storage_admin',
    'supabase_functions_admin',
    'supabase_read_only_user',
    'supabase_replication_admin',
    'supabase_admin',
    'dashboard_user'
  ];
BEGIN
  FOREACH role_name IN ARRAY roles LOOP
    BEGIN
      EXECUTE format('ALTER USER %I WITH PASSWORD %L', role_name, pw);
      RAISE NOTICE 'password actualizado: %', role_name;
    EXCEPTION
      WHEN undefined_object THEN
        RAISE NOTICE 'rol % no existe — saltado', role_name;
      WHEN insufficient_privilege THEN
        RAISE NOTICE 'sin permisos para %  — saltado', role_name;
    END;
  END LOOP;
END $$;
