-- Least-privilege grants for the application role. Run as the schema OWNER after schema.sql.
-- The app connects as bats_app, which can read and insert audit rows but can never
-- update, delete, truncate or disable the trigger on them. Only the owner can.
GRANT CONNECT ON DATABASE bats TO bats_app;
GRANT USAGE ON SCHEMA public TO bats_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO bats_app;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM bats_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO bats_app;
