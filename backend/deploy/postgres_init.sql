-- Perinatal AI: database + least-privilege roles.
-- Run as a PostgreSQL superuser:
--   psql -h 127.0.0.1 -U postgres -v migrator_pw="'...'" -v app_pw="'...'" -f deploy/postgres_init.sql
-- perinatal_migrator : owns the schema, runs Alembic migrations (DDL)
-- perinatal_app      : runtime role used by the API (DML only; audit_logs is append-only)

CREATE ROLE perinatal_migrator LOGIN PASSWORD :migrator_pw;
CREATE ROLE perinatal_app LOGIN PASSWORD :app_pw;
CREATE DATABASE perinatal_ai OWNER perinatal_migrator ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE perinatal_ai FROM PUBLIC;
GRANT CONNECT ON DATABASE perinatal_ai TO perinatal_migrator, perinatal_app;

\connect perinatal_ai
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO perinatal_migrator;
GRANT USAGE ON SCHEMA public TO perinatal_app;

-- Tables created later by the migrator are usable by the app (DML only).
ALTER DEFAULT PRIVILEGES FOR ROLE perinatal_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO perinatal_app;
ALTER DEFAULT PRIVILEGES FOR ROLE perinatal_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO perinatal_app;
