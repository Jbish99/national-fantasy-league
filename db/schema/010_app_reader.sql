-- =====================================================================
-- db/schema/010_app_reader.sql
-- Phase 5: a read-only database login for the Streamlit app.
--
-- The deployed app connects as app_reader, which can only SELECT from the
-- core schema (tables and views). It can't insert, update, delete, or create
-- anything, so nothing the app does can change the league history.
--
-- This file creates the role WITHOUT a password or login (so no secret ever
-- lands in GitHub) and grants it read access. The password is set once, from
-- your computer, by:   python -m etl.setup_app_login
--
-- Re-running this file is safe: it re-grants read access to everything in core,
-- including views created since the last run. New tables and views also get
-- read access automatically (default privileges).
--
-- Run after 009. Safe to re-run.
-- =====================================================================

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'app_reader') then
        create role app_reader nologin;   -- etl/setup_app_login.py turns on login + sets the password
    end if;
end
$$;

comment on role app_reader is
    'Read-only login for the Streamlit app: SELECT on schema core only. Password set by etl/setup_app_login.py.';

do $$
begin
    execute format('grant connect on database %I to app_reader', current_database());
end
$$;

grant usage on schema core to app_reader;
grant select on all tables in schema core to app_reader;          -- "tables" includes views
alter default privileges in schema core grant select on tables to app_reader;
-- Nothing else is granted: no raw or qa schemas, no writes, no CREATE anywhere
-- (Postgres 15+ doesn't let ordinary roles create objects in the public schema).
-- Core views that read other schemas still work, because a view runs with its owner's rights.
