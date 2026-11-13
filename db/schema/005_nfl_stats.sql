-- =====================================================================
-- db/schema/005_nfl_stats.sql
-- Phase 3: real NFL stat lines, NFL games (results, roof, weather) and the
-- link from Sleeper player ids to NFL (gsis) player ids.
--
-- Filled by etl/load_nflverse.py from nflverse (free, no key).
-- The downloaded files themselves are the raw layer for this source: they're
-- kept on your computer in data/cache/nflverse/ (git-ignored) and logged in
-- raw.nflverse_pull. Re-downloading is free, so they aren't stored in Neon
-- (decided 2026-11-13 to stay well inside the 1 GB free plan).
--
-- Run after 001-004, then re-run db/checks/001_validation.sql.
-- Safe to re-run (IF NOT EXISTS / OR REPLACE everywhere).
-- =====================================================================

-- ---------------------------------------------------------------------
-- raw: one row per nflverse download
-- ---------------------------------------------------------------------
create table if not exists raw.nflverse_pull (
    pull_id          integer generated always as identity primary key,
    dataset          text        not null,   -- ff_playerids, schedules, player_stats
    season           smallint,               -- player_stats are saved one season per file
    file_name        text        not null,   -- under data/cache/nflverse/
    row_count        integer     not null,
    column_count     integer     not null,
    file_sha256      text        not null,
    package_version  text,                   -- nflreadpy version that downloaded it
    pulled_at        timestamptz not null default now()
);
comment on table raw.nflverse_pull is
    'Log of nflverse downloads. The files themselves live in data/cache/nflverse/ on the computer that ran the loader.';


-- ---------------------------------------------------------------------
-- core.player_xref: Sleeper player id -> NFL gsis id
-- ---------------------------------------------------------------------
create table if not exists core.player_xref (
    player_id   text        primary key references core.player,   -- Sleeper id
    gsis_id     text        not null,                              -- NFL id used by nflverse stat lines
    method      text        not null check (method in ('crosswalk', 'sleeper', 'stats_checked', 'stats_name', 'manual')),
    note        text,                                              -- why, when it wasn't straightforward
    updated_at  timestamptz not null default now()
);
create index if not exists ix_player_xref_gsis on core.player_xref (gsis_id);
comment on table core.player_xref is
    'Sleeper player id -> NFL gsis id. method: crosswalk (nflverse ID crosswalk), sleeper (Sleeper''s own gsis_id), '
    'stats_checked (the two disagreed; picked the id whose stat lines carry this player''s name), '
    'stats_name (no id anywhere; matched by name + position in the stat lines), manual (data/manual/nfl_id_overrides.csv).';


-- ---------------------------------------------------------------------
-- core.nfl_game: every NFL game, with result, roof, surface and weather
-- ---------------------------------------------------------------------
create table if not exists core.nfl_game (
    game_id     text primary key,   -- nflverse game id, e.g. 2025_01_DAL_PHI
    season          smallint not null,
    game_type       text not null,
    week            smallint not null,
    gameday         date,
    weekday         text,
    gametime        text,
    away_team       text not null,
    home_team       text not null,
    away_score      smallint,
    home_score      smallint,
    overtime        boolean,
    location        text,
    roof            text,
    surface         text,
    temp            smallint,
    wind            smallint,
    stadium_id      text,
    stadium         text,
    div_game        boolean,
    spread_line     numeric,
    total_line      numeric,
    away_moneyline  integer,
    home_moneyline  integer,
    away_rest       smallint,
    home_rest       smallint,
    away_qb_id      text,
    home_qb_id      text,
    away_qb_name    text,
    home_qb_name    text,
    away_coach      text,
    home_coach      text,
    referee         text,
    pfr_game_id     text,
    espn_game_id    text,
    source      text not null default 'nflverse',
    loaded_at   timestamptz not null default now()
);
create index if not exists ix_nfl_game_season_week on core.nfl_game (season, week);
comment on table core.nfl_game is
    'Every NFL game 2020 ->: teams, scores (null until played), roof, surface, temperature (F), wind (mph), betting lines. '
    'temp/wind are null for domes and closed roofs, and missing for some outdoor games in the source.';


-- ---------------------------------------------------------------------
-- core.nfl_player_week: real stat lines, one row per player per NFL week
-- ---------------------------------------------------------------------
create table if not exists core.nfl_player_week (
    gsis_id         text     not null,
    season          smallint not null,
    week            smallint not null,   -- NFL week; regular season 1-18, then playoffs keep counting
    season_type     text     not null,   -- REG or POST
    game_id         text,                -- links to core.nfl_game
    player_name     text,
    position        text,
    position_group  text,
    team            text,
    opponent_team   text,
    completions                  integer,
    attempts                     integer,
    passing_yards                integer,
    passing_tds                  integer,
    passing_interceptions        integer,
    sacks_suffered               integer,
    sack_yards_lost              integer,
    sack_fumbles                 integer,
    sack_fumbles_lost            integer,
    passing_air_yards            integer,
    passing_yards_after_catch    integer,
    passing_first_downs          integer,
    passing_2pt_conversions      integer,
    passing_epa                  double precision,
    passing_cpoe                 double precision,
    pacr                         double precision,
    carries                      integer,
    rushing_yards                integer,
    rushing_tds                  integer,
    rushing_fumbles              integer,
    rushing_fumbles_lost         integer,
    rushing_first_downs          integer,
    rushing_2pt_conversions      integer,
    rushing_epa                  double precision,
    receptions                   integer,
    targets                      integer,
    receiving_yards              integer,
    receiving_tds                integer,
    receiving_fumbles            integer,
    receiving_fumbles_lost       integer,
    receiving_air_yards          integer,
    receiving_yards_after_catch  integer,
    receiving_first_downs        integer,
    receiving_2pt_conversions    integer,
    receiving_epa                double precision,
    racr                         double precision,
    target_share                 double precision,
    air_yards_share              double precision,
    wopr                         double precision,
    fumbles_total                integer,
    fumbles_lost_total           integer,
    special_teams_tds            integer,
    fumble_recovery_tds          integer,
    punt_returns                 integer,
    punt_return_yards            integer,
    kickoff_returns              integer,
    kickoff_return_yards         integer,
    fg_made                      integer,
    fg_att                       integer,
    fg_missed                    integer,
    fg_blocked                   integer,
    fg_long                      integer,
    fg_made_0_19                 integer,
    fg_made_20_29                integer,
    fg_made_30_39                integer,
    fg_made_40_49                integer,
    fg_made_50_59                integer,
    fg_made_60_                  integer,
    fg_missed_0_19               integer,
    fg_missed_20_29              integer,
    fg_missed_30_39              integer,
    fg_missed_40_49              integer,
    fg_missed_50_59              integer,
    fg_missed_60_                integer,
    fg_made_distance             integer,
    pat_made                     integer,
    pat_att                      integer,
    pat_missed                   integer,
    pat_blocked                  integer,
    fantasy_points               numeric,
    fantasy_points_ppr           numeric,
    source          text not null default 'nflverse',
    loaded_at       timestamptz not null default now(),
    primary key (gsis_id, season, week)
);
create index if not exists ix_nfl_player_week_game on core.nfl_player_week (game_id);
comment on table core.nfl_player_week is
    'Real NFL stat lines by player and week (QB, RB, WR, TE, K, FB, plus anyone who was on a league roster). '
    'fantasy_points / fantasy_points_ppr are nflverse''s standard and full-PPR points, not our league''s scoring.';


-- ---------------------------------------------------------------------
-- core.v_player_week_full: Sleeper week + real stat line + game conditions
-- ---------------------------------------------------------------------
create or replace view core.v_player_week_full as
select
    pw.season,
    pw.week,
    pw.franchise_code,
    pw.player_id,
    p.full_name,
    p.position,
    pw.lineup_slot,
    pw.is_starter,
    pw.points            as league_points,      -- what Sleeper scored for our league
    x.gsis_id,
    s.team               as nfl_team,
    s.opponent_team,
    s.game_id,
    s.completions, s.attempts, s.passing_yards, s.passing_tds, s.passing_interceptions,
    s.carries, s.rushing_yards, s.rushing_tds,
    s.targets, s.receptions, s.receiving_yards, s.receiving_tds,
    s.fumbles_lost_total,
    s.fg_made, s.fg_att, s.pat_made, s.pat_att,
    g.gameday, g.roof, g.surface, g.temp, g.wind,
    (s.gsis_id is not null) as has_stat_line   -- false: didn't play that week (bye, injured, inactive) or no NFL id
from core.player_week pw
join core.player p          on p.player_id = pw.player_id
left join core.player_xref x on x.player_id = pw.player_id
left join core.nfl_player_week s
       on s.gsis_id = x.gsis_id
      and s.season = pw.season
      and s.week = pw.week
      and s.season_type = 'REG'
left join core.nfl_game g    on g.game_id = s.game_id;

comment on view core.v_player_week_full is
    'Every rostered player-week from Sleeper with the player''s real stat line and game conditions (when he played).';
