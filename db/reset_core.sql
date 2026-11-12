-- =====================================================================
-- db/reset_core.sql
-- Drops the clean layer (core) and the checks (qa) so they can be
-- rebuilt from the schema files. The raw layer is NOT touched, so
-- nothing pulled from Sleeper or the workbook is lost.
--
-- After running this, run in order:
--   db/schema/001_raw.sql, 002_core.sql, 003_views.sql,
--   db/checks/001_validation.sql, db/seed/001_reference_data.sql
-- then re-run the loaders.
-- =====================================================================

drop schema if exists qa cascade;
drop schema if exists core cascade;
