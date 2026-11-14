-- =====================================================================
-- db/schema/007_nfl_team_stats.sql
-- Phase 3: each NFL team's weekly defense / special-teams stat line, so the
-- league's Sleeper defenses (DEF players) have a real stat line too.
-- Filled by the 'teams' step of etl/load_nflverse.py (nflverse weekly team stats).
--
-- Rebuilding DEF fantasy points from these is parked for the Advanced
-- Statistics stage (the league also scores 3-and-outs and 4th-down stops,
-- which need play-by-play data). Sleeper's DEF points are already stored in
-- core.player_week.
--
-- Run after 006. Safe to re-run.
-- =====================================================================

create table if not exists core.nfl_team_week (
    team                 text     not null,   -- nflverse team code (LA = Rams)
    season               smallint not null,
    week                 smallint not null,
    season_type          text     not null,   -- REG or POST
    game_id              text,                -- links to core.nfl_game
    opponent_team        text,
    sleeper_def_id       text     not null,   -- Sleeper's id for this defense (LA -> LAR)
    def_sacks               numeric,
    def_sack_yards          numeric,
    def_qb_hits             integer,
    def_interceptions       integer,
    def_interception_yards  integer,
    def_fumbles_forced      integer,
    fumble_recovery_opp     integer,
    fumble_recovery_tds     integer,
    def_tds                 integer,
    def_safeties            integer,
    def_tackles_for_loss    integer,
    def_pass_defended       integer,
    def_punt_blocks         integer,
    def_fg_blocks           integer,
    def_pat_blocks          integer,
    def_2pt_made            integer,
    special_teams_tds       integer,
    punt_return_yards       integer,
    kickoff_return_yards    integer,
    opp_passing_yards    integer,             -- what the opponent's offense gained:
    opp_sack_yards_lost  integer,             --   (negative number)
    opp_rushing_yards    integer,
    net_yards_allowed    integer,             -- opp passing + sack yards + rushing = NFL net yards
    source               text not null default 'nflverse',
    loaded_at            timestamptz not null default now(),
    primary key (team, season, week)
);
create index if not exists ix_nfl_team_week_def on core.nfl_team_week (sleeper_def_id, season, week);
comment on table core.nfl_team_week is
    'Each NFL team''s weekly defense and special-teams stat line, plus the yards its opponent gained. sleeper_def_id links to Sleeper DEF players.';


create or replace view core.v_def_week_full as
select
    pw.season,
    pw.week,
    pw.franchise_code,
    pw.player_id                         as defense,
    pw.lineup_slot,
    pw.is_starter,
    pw.points                            as league_points,
    t.opponent_team,
    case when g.home_team = t.team then g.away_score else g.home_score end as points_allowed,
    t.net_yards_allowed,
    t.def_sacks, t.def_interceptions, t.fumble_recovery_opp, t.def_fumbles_forced,
    t.def_tds, t.special_teams_tds, t.def_safeties,
    t.def_punt_blocks + t.def_fg_blocks + t.def_pat_blocks as blocked_kicks,
    t.def_tackles_for_loss, t.def_pass_defended,
    g.gameday, g.roof, g.temp, g.wind,
    (t.team is not null)                 as has_stat_line   -- false on a bye week
from core.player_week pw
join core.player p on p.player_id = pw.player_id and p.position = 'DEF'
left join core.nfl_team_week t
       on t.sleeper_def_id = pw.player_id and t.season = pw.season and t.week = pw.week and t.season_type = 'REG'
left join core.nfl_game g on g.game_id = t.game_id;

comment on view core.v_def_week_full is
    'Every rostered Sleeper defense-week with that NFL team''s real defensive stat line and the points it allowed.';
