-- =====================================================================
-- db/schema/012_player_game_log.sql
-- Phase 7a: one row per rostered player-week (2021 ->, Sleeper) with everything
-- the Player page, the Box score page and the Record book's player records need:
--   * what Sleeper scored for our league, the lineup slot, started or not
--   * who had him, and the official league game his team played that week
--   * his real NFL stat line (offense/kicking from core.nfl_player_week, or the
--     team defense line from core.nfl_team_week for DEF), the NFL opponent,
--     the NFL score, and the game's roof / temperature / wind
--
-- Built only on tables that already exist (no new data). Row count = core.player_week:
-- every join below is one-to-one (qa.player_log_duplicates checks it).
--
-- Notes:
--   * league_points are Sleeper's points (the league's record). The NFL stat line
--     can disagree in rare cases (late stat corrections); points always come from Sleeper.
--   * No stat line (has_stat_line = false): he didn't play that week, or played and
--     recorded no stats (nflverse only lists players with at least one stat).
--   * nfl_team / nfl_opponent use nflverse codes (LA = Rams, LV = Raiders).
--   * def_points_allowed = what the NFL opponent scored in that game.
--   * 2020 (ESPN) has no weekly player data, so it isn't here.
--
-- Run after 011. Safe to re-run.
-- =====================================================================

create or replace view core.v_player_game_log as
select
    pw.season,
    pw.week,
    (pw.week < s.playoff_week_start)                                    as is_regular_season,
    pw.franchise_code,
    pw.player_id,
    coalesce(p.full_name, trim(coalesce(p.first_name, '') || ' ' || coalesce(p.last_name, '')))
                                                                        as player_name,   -- defenses have no full_name
    p.position,
    pw.lineup_slot,
    pw.slot_index,
    pw.is_starter,
    pw.points                                                           as league_points,

    -- the official league game his team played that week (null = no official game that week)
    tg.game_id                                                          as league_game_id,
    tg.game_type                                                        as league_game_type,
    tg.designation                                                      as league_designation,
    tg.opponent_code                                                    as league_opponent_code,
    tg.result                                                           as league_result,

    -- the NFL game
    coalesce(n.team, d.team)                                            as nfl_team,
    coalesce(n.opponent_team, d.opponent_team)                          as nfl_opponent,
    g.game_id                                                           as nfl_game_id,
    (g.home_team = coalesce(n.team, d.team))                            as is_home,
    g.location                                                          as nfl_location,  -- Home or Neutral
    case when g.home_team = coalesce(n.team, d.team) then g.home_score else g.away_score end as nfl_team_score,
    case when g.home_team = coalesce(n.team, d.team) then g.away_score else g.home_score end as nfl_opponent_score,
    g.gameday,
    g.roof,                                                             -- outdoors, dome, closed, open
    g.temp,                                                             -- degrees F; null indoors and for some outdoor games
    g.wind,                                                             -- mph; same gaps as temp

    -- offense and kicking (null for defenses and when there's no stat line)
    n.completions, n.attempts, n.passing_yards, n.passing_tds, n.passing_interceptions, n.sacks_suffered,
    n.carries, n.rushing_yards, n.rushing_tds,
    n.targets, n.receptions, n.receiving_yards, n.receiving_tds,
    case when n.gsis_id is not null then
        coalesce(n.sack_fumbles_lost, 0) + coalesce(n.rushing_fumbles_lost, 0) + coalesce(n.receiving_fumbles_lost, 0)
    end                                                                 as fumbles_lost,
    case when n.gsis_id is not null then
        coalesce(n.passing_2pt_conversions, 0) + coalesce(n.rushing_2pt_conversions, 0)
        + coalesce(n.receiving_2pt_conversions, 0)
    end                                                                 as two_point_conversions,
    n.special_teams_tds                                                 as return_tds,
    n.fg_made, n.fg_att, n.fg_long, n.pat_made, n.pat_att,

    -- team defense (DEF only)
    d.def_sacks,
    d.def_interceptions,
    d.fumble_recovery_opp                                               as def_fumble_recoveries,
    case when d.team is not null then coalesce(d.def_tds, 0) + coalesce(d.special_teams_tds, 0) end
                                                                        as def_tds,       -- defensive + return TDs
    d.def_safeties,
    case when d.team is not null then
        coalesce(d.def_punt_blocks, 0) + coalesce(d.def_fg_blocks, 0) + coalesce(d.def_pat_blocks, 0)
    end                                                                 as def_blocked_kicks,
    case when d.team is not null then
        case when g.home_team = d.team then g.away_score else g.home_score end
    end                                                                 as def_points_allowed,
    d.net_yards_allowed                                                 as def_yards_allowed,

    (n.gsis_id is not null or d.team is not null)                       as has_stat_line
from core.player_week pw
join core.season s           on s.season = pw.season
join core.player p           on p.player_id = pw.player_id
left join core.v_team_game tg
       on tg.season = pw.season and tg.week = pw.week and tg.franchise_code = pw.franchise_code
left join core.player_xref x
       on x.player_id = pw.player_id and p.position is distinct from 'DEF'
left join core.nfl_player_week n
       on n.gsis_id = x.gsis_id and n.season = pw.season and n.week = pw.week and n.season_type = 'REG'
left join core.nfl_team_week d
       on p.position = 'DEF' and d.sleeper_def_id = pw.player_id
      and d.season = pw.season and d.week = pw.week and d.season_type = 'REG'
left join core.nfl_game g    on g.game_id = coalesce(n.game_id, d.game_id);

comment on view core.v_player_game_log is
    'Every rostered player-week (2021 ->): Sleeper points, lineup slot, who had him, the league game, and his real NFL '
    'stat line (or team defense line), NFL opponent and score, roof, temperature and wind. Used by the Player, '
    'Box score and Record book pages.';
