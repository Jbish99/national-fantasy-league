-- =====================================================================
-- db/schema/001_raw.sql
-- RAW layer: API responses stored exactly as received, plus when we
-- pulled them. Nothing in the app reads from here. It exists so the
-- clean tables can always be rebuilt without calling the APIs again.
--
-- Safe to re-run: everything uses IF NOT EXISTS / OR REPLACE.
-- =====================================================================

create schema if not exists raw;
comment on schema raw is 'API responses stored as-is with pull timestamps. Source for rebuilding the core layer.';

-- ---------------------------------------------------------------------
-- raw.sleeper_response
-- One row per distinct response body per URL.
--   * Same URL + same body pulled again  -> no new row, last_pulled_at updates.
--   * Same URL + different body (stat correction, new week) -> new row,
--     so we keep a history of what Sleeper said and when.
-- Loaders insert with:
--   insert ... on conflict (request_url, payload_hash)
--   do update set last_pulled_at = now();
-- ---------------------------------------------------------------------
create table if not exists raw.sleeper_response (
    response_id      bigint generated always as identity primary key,
    endpoint         text        not null,  -- league, users, rosters, matchups, transactions, winners_bracket,
                                            -- losers_bracket, drafts, draft_picks, traded_picks, players, state
    request_url      text        not null,  -- full URL that was called
    season           smallint,              -- null for players and state
    week             smallint,              -- matchups and transactions only
    payload          jsonb       not null,  -- response body, untouched
    payload_hash     text        generated always as (md5(payload::text)) stored,
    first_pulled_at  timestamptz not null default now(),
    last_pulled_at   timestamptz not null default now(),
    constraint uq_sleeper_response unique (request_url, payload_hash)
);

comment on table raw.sleeper_response is
    'Every Sleeper API response as received. New row only when the body changes. /players/nfl is big, so refresh it at most weekly and keep only the newest copy.';

create index if not exists ix_sleeper_response_lookup
    on raw.sleeper_response (endpoint, season, week, last_pulled_at desc);

-- Latest body for each URL. This is what the clean-layer loaders read.
create or replace view raw.v_sleeper_latest as
select distinct on (request_url)
       response_id, endpoint, request_url, season, week, payload, last_pulled_at
from raw.sleeper_response
order by request_url, last_pulled_at desc, response_id desc;

comment on view raw.v_sleeper_latest is 'Most recent response for each Sleeper URL.';


-- ---------------------------------------------------------------------
-- raw.workbook_row
-- Every data row from the league's history workbook (All-In-One.xlsx),
-- one row per spreadsheet row, stored as {column header: value}.
-- etl/load_workbook.py replaces a sheet's rows each time it runs.
-- ---------------------------------------------------------------------
create table if not exists raw.workbook_row (
    workbook     text        not null,  -- file name, e.g. 'All-In-One.xlsx'
    sheet        text        not null,  -- 'Scores', 'Draft', 'Rankings', ...
    row_number   integer     not null,  -- the row number you'd see in Excel
    data         jsonb       not null,  -- {"Game ID": "2025-15-DET-OAK", "Year": 2025, ...}
    file_sha256  text        not null,  -- fingerprint of the file this came from
    loaded_at    timestamptz not null default now(),
    primary key (workbook, sheet, row_number)
);

comment on table raw.workbook_row is
    'Rows from the official league history workbook (All-In-One.xlsx), stored as-is per sheet.';
