-- CI plain Postgres only: create the Supabase API roles if they are missing so
-- migrations and the exposed-schema check behave as on Supabase. Idempotent.
-- Never run this against a Supabase project (the roles already exist there).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN NOINHERIT;
  END IF;
END
$$;
