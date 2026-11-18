-- Run as perinatal_migrator after every `alembic upgrade head`.
-- Makes the audit trail append-only for the runtime role (defence in depth on top of the hash chain).
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM perinatal_app;
GRANT SELECT, INSERT ON audit_logs TO perinatal_app;
-- Privacy events are append-only as well.
REVOKE UPDATE, DELETE, TRUNCATE ON privacy_events FROM perinatal_app;
GRANT SELECT, INSERT ON privacy_events TO perinatal_app;
-- The runtime role never needs the migration bookkeeping table.
REVOKE ALL ON alembic_version FROM perinatal_app;
