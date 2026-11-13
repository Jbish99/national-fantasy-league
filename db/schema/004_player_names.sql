-- =====================================================================
-- db/schema/004_player_names.sql
-- Player-name matching for the workbook (keepers, 2020 draft, awards),
-- and room for the 2020 draft's gaps.
--
-- Run after 001-003. Safe to re-run (IF NOT EXISTS everywhere), and safe
-- to run on a database that already has data: it only adds things.
-- =====================================================================

-- ---------------------------------------------------------------------
-- core.player_name_map
-- How each player name typed in the workbook maps to a Sleeper player id.
-- Built by etl/match_players.py (the 'names' step of load_core), which
-- also writes it to data/manual/player_name_map.csv for review in Excel.
--
-- status:
--   auto          matched with confidence (exact name on that team's roster, etc.)
--   check         probably right (close spelling); please confirm
--   unmatched     no good match; needs a player id from you
--   confirmed     you checked it (kept as-is on every re-run)
--   not_a_player  you marked it as not an NFL player (a coach, an owner...)
-- ---------------------------------------------------------------------
create table if not exists core.player_name_map (
    source           text     not null check (source in ('keeper', 'award', 'draft')),
    season           smallint not null,
    team_code        text,                  -- franchise the name was listed under; null if none / AFC / NFC / unknown
    name_as_entered  text     not null,     -- exactly as typed in the workbook
    position         text,                  -- as typed (QB, WR, FX, BENCH, DST...)
    player_id        text     references core.player,
    matched_name     text,                  -- Sleeper's name for player_id, to eyeball
    method           text,                  -- how it was matched (team_roster_exact, all_players_close, ...)
    score            numeric(4,3),          -- 1.000 = exact
    status           text     not null check (status in ('auto', 'check', 'unmatched', 'confirmed', 'not_a_player')),
    candidates       text,                  -- best alternatives, to help pick the right id
    constraint uq_player_name_map unique nulls not distinct (source, season, team_code, name_as_entered)
);
comment on table core.player_name_map is
    'Workbook player names -> Sleeper player ids, with how sure the match is. Review copy: data/manual/player_name_map.csv.';


-- ---------------------------------------------------------------------
-- core.draft_pick: room for the 2020 (ESPN) draft from the workbook,
-- where 43 picks have no drafting team and pick numbers are mostly missing.
-- ---------------------------------------------------------------------
alter table core.draft_pick alter column franchise_code drop not null;
alter table core.draft_pick add column if not exists player_name_as_entered text;
alter table core.draft_pick add column if not exists pick_no_is_estimated boolean not null default false;

comment on column core.draft_pick.franchise_code is 'Team that made the pick. Null only for 2020 picks the workbook marks "?".';
comment on column core.draft_pick.player_name_as_entered is 'Player name as typed in the workbook (2020 draft).';
comment on column core.draft_pick.pick_no_is_estimated is 'True when pick_no is the workbook row order, not a recorded pick number (2020).';
