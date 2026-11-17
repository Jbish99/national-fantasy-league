-- =====================================================================
-- db/schema/018_speed.sql
-- Phase 11: speed-up. Nothing here changes the history or any view; it adds the
-- bookkeeping for two caches.
--
--   1. core.data_version: a one-row stamp. Every load that changes core data (load_core,
--      load_nflverse) and every run_sql bumps it (etl/precompute.py). The app checks it about
--      once a minute and keeps every query result cached until it changes (at most 24 hours),
--      so new data shows right after a refresh and nobody waits for the same queries twice.
--      (Jaden, 2026-11-16: "new data visible right after each refresh".)
--
--   2. core.precomputed: the slowest views, each with a precomputed copy: a plain table
--      named core.copy_<view name without v_>, rebuilt by etl/precompute.py at the end of
--      every load and of the daily refresh. THE VIEW STAYS THE SOURCE OF TRUTH. A copy is only
--      used when it was built for the current data version AND checked row for row against
--      its view (status 'ok'); in every other case the app reads the view itself. So a stale
--      or broken copy can't show wrong history; at worst a page is slower.
--      To give another view a copy: add a row below (index_columns = the columns pages filter
--      on), then run_sql --all. To drop one: delete its row; etl/precompute.py removes the copy.
--
-- New here: core.data_version, core.precomputed, core.copy_differences().
-- qa: qa.precomputed_problem (warn-only) and qa.precomputed_compare (on demand) live in
-- db/checks/001_validation.sql.
--
-- Run after 017. Safe to re-run.
-- =====================================================================

create table if not exists core.data_version (
    one_row     boolean primary key default true check (one_row),
    version     bigint not null default 1,
    stamped_at  timestamptz not null default now(),
    stamped_by  text
);
insert into core.data_version (one_row, stamped_by) values (true, '018_speed.sql') on conflict do nothing;

comment on table core.data_version is
    'Phase 11: one row. version goes up whenever core data or a view definition changes (etl/precompute.py, '
    'called by load_core, load_nflverse and run_sql). The app keys its cache on it and uses a precomputed copy '
    'only when the copy was built for this version.';

create table if not exists core.precomputed (
    view_name      text primary key,              -- the source of truth, e.g. core.v_franchise_identity
    copy_name      text not null unique,          -- a plain table, e.g. core.copy_franchise_identity
    index_columns  text[] not null default '{}',  -- one index per entry, e.g. {player_id} or {"season, week"}
    sort_order     integer not null,
    data_version   bigint,                        -- the version it was built + checked for; null = don't use
    status         text not null default 'not built yet',   -- ok / differs from its view / failed / view missing
    row_count      bigint,
    built_at       timestamptz,
    build_seconds  numeric(8, 2)
);

comment on table core.precomputed is
    'Phase 11: views with a precomputed copy (a plain table rebuilt by etl/precompute.py). The app reads the copy '
    'instead of the view only when status = ''ok'' and data_version = core.data_version.version; otherwise the view.';

-- The list (Phase 11 measurements on Neon, 2026-11-16: each of these took 0.3-1.7 s per query).
-- Order = build order. Index columns = what pages filter on.
insert into core.precomputed (view_name, copy_name, index_columns, sort_order) values
    ('core.v_team_metric',             'core.copy_team_metric',             '{}',                        10),
    ('core.v_team_identity',           'core.copy_team_identity',           '{franchise_code}',          20),
    ('core.v_franchise_identity',      'core.copy_franchise_identity',      '{franchise_code}',          30),
    ('core.v_trade_side',              'core.copy_trade_side',              '{transaction_id}',          40),
    ('core.v_trade_asset_value',       'core.copy_trade_asset_value',       '{transaction_id}',          50),
    ('core.v_trade_asset_week',        'core.copy_trade_asset_week',        '{transaction_id}',          60),
    ('core.v_position_conditions',     'core.copy_position_conditions',     '{}',                        70),
    ('core.v_player_week_conditions',  'core.copy_player_week_conditions',  '{gsis_id}',                 80),
    ('core.v_player_week_extras',      'core.copy_player_week_extras',      '{player_id,"season, week"}', 90),
    ('core.v_draft_value',             'core.copy_draft_value',             '{season}',                 100)
on conflict (view_name) do update
    set copy_name = excluded.copy_name, index_columns = excluded.index_columns, sort_order = excluded.sort_order;

-- How many rows differ between a view and its copy (rows in one and not the other, duplicates counted).
-- 0 = identical. Used by etl/precompute.py right after building a copy, and by qa.precomputed_compare.
create or replace function core.copy_differences(p_view regclass, p_copy regclass)
returns bigint
language plpgsql
stable
as $$
declare
    n bigint;
begin
    execute format(
        'select count(*) from ((table %1$s except all table %2$s) union all (table %2$s except all table %1$s)) d',
        p_view, p_copy)
    into n;
    return n;
end
$$;

comment on function core.copy_differences(regclass, regclass) is
    'Phase 11: rows that differ between a view and its precomputed copy (0 = identical).';

-- The app's read-only login reads the stamp and the list (010 runs before this file, so grant here too;
-- the copies themselves are granted by etl/precompute.py when it builds them).
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'app_reader') then
        grant select on core.data_version, core.precomputed to app_reader;
    end if;
end
$$;
