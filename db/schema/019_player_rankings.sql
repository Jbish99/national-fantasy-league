-- =====================================================================
-- db/schema/019_player_rankings.sql
-- Phase 12: player rankings. Where a performance or a season stands among EVERY NFL player at
-- that position, in our league's scoring (Jaden, 2026-11-17: "adding just another column to already
-- existing sheets, showing where a player performance ranked out of all players in that same position
-- in a week ... An all time rankings of players based on stats would be cool too.").
--
--   core.v_league_season_weeks   the league's season per year: weeks 1 through its last playoff week (17)
--   core.v_player_week_rank      every NFL player-week (2021 ->) with his weekly position rank ("WR2")
--   core.v_player_season_rank    every player-season: totals, per game, consistency, real stats, ranks
--   core.v_player_career_rank    the same added up over 2021 on (all-time lists)
--   core.v_position_group_rank   each league team's position groups ranked 1-12 each season (Positions page)
--
-- Rules (Claude's calls, 2026-11-17; Jaden can change them, see claude/roadmap.md Phase 12):
--   * Points = our league's points for that season, rebuilt from each NFL stat line
--     (core.v_nfl_league_points; DEF from core.v_def_league_points). One measure for every NFL player,
--     rostered or not. They equal Sleeper's for ~99% of player-weeks; the pages show Sleeper's points
--     and say so in the hover text when the two differ.
--   * Pool = every NFL player with a stat line that week (regular season, 2021 on). A player who didn't
--     play, or played without recording a stat, has no rank that week.
--   * Position = Sleeper's (what our lineups and position chips use; FB counts as RB), else nflverse's.
--     A position is ranked only in seasons the league had a lineup slot for it (kickers from 2026).
--   * Ties share a rank (1, 2, 2, 4): the pages show "T-WR12".
--   * Season = the weeks the league plays: NFL weeks 1 through the last playoff week (17). Week 18
--     doesn't count for anyone. Weekly ranks exist for week 18 too, but no league page shows them.
--   * Per-game lists need a minimum: 6 games in a season (or half the weeks so far in a season in
--     progress), 17 games all-time (MIN_GAMES_* below).
--   * Points over replacement = points minus what a replacement-level player at his position scores
--     over the same weeks (core.v_replacement_level's per-week level, as the Trade analyzer uses).
--   * Starter weeks = weeks he finished inside the top N at his position, N = how many our league starts
--     there (the replacement rank: QB12, RB34, WR34, TE17, K12, DEF12). Consistency = share of his games.
--   * Boom week = 20+ points. Bust week = under 5 points.
--
-- Run after 018. Safe to re-run.
-- =====================================================================


-- ---------------------------------------------------------------------
-- The league's season: weeks 1 through the last week a league game was played (the last playoff
-- week). A season whose playoffs haven't happened yet uses playoff_week_start + rounds - 1.
-- ---------------------------------------------------------------------
create or replace view core.v_league_season_weeks as
select
    s.season,
    s.playoff_week_start,
    coalesce(post.last_week,
             s.playoff_week_start
             + ceil(log(2, greatest(coalesce(s.playoff_teams, 2), 2)::numeric))::int - 1) as last_week
from core.season s
left join (select season, max(week) as last_week
           from core.game
           where game_type <> 'regular'
           group by season) post on post.season = s.season
where s.platform = 'sleeper'
  and s.playoff_week_start is not null;

comment on view core.v_league_season_weeks is
    'Phase 12: per Sleeper season, the weeks the league plays (1 through the last playoff week). Season rankings '
    'count these weeks only.';


-- ---------------------------------------------------------------------
-- Weekly position ranks: every NFL player-week, ranked within its position that week.
-- ---------------------------------------------------------------------
create or replace view core.v_player_week_rank as
with sleeper as (            -- Sleeper id, name and position of each linked NFL player
    select distinct on (x.gsis_id)
           x.gsis_id, x.player_id, p.position,
           coalesce(p.full_name, nullif(trim(coalesce(p.first_name, '') || ' ' || coalesce(p.last_name, '')), '')) as name
    from core.player_xref x
    join core.player p on p.player_id = x.player_id
    order by x.gsis_id, x.player_id
),
offense as (
    select lp.gsis_id, lp.season, lp.week, lp.player_name, lp.position as nfl_position, lp.team, lp.league_points
    from core.v_nfl_league_points lp
    where lp.season_type = 'REG'
      and lp.season in (select season from core.scoring_setting)       -- Sleeper seasons (2020 was ESPN)
),
season_position as (         -- one position per player-season, so his weekly and season ranks use the same pool
    select n.gsis_id, n.season, mode() within group (order by n.position) as nfl_position
    from core.nfl_player_week n
    where n.season_type = 'REG'
    group by n.gsis_id, n.season
),
pool as (
    select o.season, o.week, o.gsis_id as rank_id, o.gsis_id, s.player_id,
           coalesce(s.name, o.player_name) as player_name,
           case coalesce(s.position, sp.nfl_position) when 'FB' then 'RB'
                else coalesce(s.position, sp.nfl_position) end as position,
           o.team as nfl_team,
           o.league_points as points
    from offense o
    join season_position sp on sp.gsis_id = o.gsis_id and sp.season = o.season
    left join sleeper s on s.gsis_id = o.gsis_id
    union all
    select d.season, d.week, d.sleeper_def_id, null, d.sleeper_def_id,
           coalesce(p.full_name, nullif(trim(coalesce(p.first_name, '') || ' ' || coalesce(p.last_name, '')), ''),
                    d.sleeper_def_id),
           'DEF', d.team, d.league_points
    from core.v_def_league_points d
    left join core.player p on p.player_id = d.sleeper_def_id
    where d.sleeper_def_id is not null
)
select
    p.season,
    p.week,
    p.rank_id,                       -- gsis_id for players, the Sleeper defense id (e.g. DET) for DEF
    p.gsis_id,
    p.player_id,                     -- Sleeper id (null: an NFL player not linked to Sleeper)
    p.player_name,
    p.position,
    p.nfl_team,
    p.points,                        -- league points rebuilt from his stat line
    rank()   over (partition by p.season, p.week, p.position order by p.points desc)  as pos_rank,
    count(*) over (partition by p.season, p.week, p.position)                          as pos_count,
    count(*) over (partition by p.season, p.week, p.position, p.points) > 1           as pos_tied
from pool p
where exists (select 1 from core.roster_slot rs where rs.season = p.season and rs.slot = p.position);

comment on view core.v_player_week_rank is
    'Phase 12: every NFL regular-season player-week (2021 ->) scored with league rules, ranked within its position '
    'that week among every NFL player with a stat line (ties share a rank). pos_rank 2 = "WR2 that week".';


-- ---------------------------------------------------------------------
-- Season rankings: one row per player-season over the league's season (weeks 1-17).
-- ---------------------------------------------------------------------
create or replace view core.v_player_season_rank as
with league_weeks as materialized (
    select season, last_week from core.v_league_season_weeks
),
weeks as materialized (
    select w.*
    from core.v_player_week_rank w
    join league_weeks lw on lw.season = w.season and w.week <= lw.last_week
),
season_weeks as materialized (            -- weeks of the league's season with NFL stats so far (17 in a finished season)
    select season, count(distinct week) as weeks
    from weeks
    group by season
),
replacement as materialized (
    select season, position, replacement_rank, per_week from core.v_replacement_level
),
fantasy as materialized (
    select w.season, w.rank_id,
           max(w.gsis_id)                                               as gsis_id,
           max(w.player_id)                                             as player_id,
           max(w.player_name)                                           as player_name,
           max(w.position)                                              as position,
           (array_agg(w.nfl_team order by w.week desc))[1]              as nfl_team,   -- his latest NFL team
           count(*)                                                     as games,
           sum(w.points)                                                as points,
           max(w.points)                                                as best_week,
           min(w.pos_rank)                                              as best_week_rank,
           count(*) filter (where w.pos_rank = 1)                       as top1_weeks,
           count(*) filter (where w.pos_rank <= r.replacement_rank)     as starter_weeks,
           count(*) filter (where w.points >= 20)                       as boom_weeks,
           count(*) filter (where w.points < 5)                         as bust_weeks,
           max(r.replacement_rank)                                      as starter_line,
           max(r.per_week)                                              as replacement_per_week
    from weeks w
    left join replacement r on r.season = w.season and r.position = w.position
    group by w.season, w.rank_id
),
team_week as materialized (               -- each NFL offense's carries and targets per game (for shares)
    select team, season, week, sum(coalesce(carries, 0)) as carries, sum(coalesce(targets, 0)) as targets
    from core.nfl_player_week
    where season_type = 'REG'
    group by team, season, week
),
real_stats as materialized (
    select n.gsis_id, n.season,
           sum(coalesce(n.completions, 0))            as completions,
           sum(coalesce(n.attempts, 0))               as pass_attempts,
           sum(coalesce(n.passing_yards, 0))          as passing_yards,
           sum(coalesce(n.passing_tds, 0))            as passing_tds,
           sum(coalesce(n.passing_interceptions, 0))  as interceptions,
           sum(coalesce(n.carries, 0))                as carries,
           sum(coalesce(n.rushing_yards, 0))          as rushing_yards,
           sum(coalesce(n.rushing_tds, 0))            as rushing_tds,
           sum(coalesce(n.targets, 0))                as targets,
           sum(coalesce(n.receptions, 0))             as receptions,
           sum(coalesce(n.receiving_yards, 0))        as receiving_yards,
           sum(coalesce(n.receiving_tds, 0))          as receiving_tds,
           sum(coalesce(n.sack_fumbles_lost, 0) + coalesce(n.rushing_fumbles_lost, 0)
               + coalesce(n.receiving_fumbles_lost, 0)) as fumbles_lost,
           sum(coalesce(n.fg_made, 0))                as fg_made,
           sum(coalesce(n.fg_att, 0))                 as fg_att,
           max(n.fg_long)                             as fg_long,
           sum(coalesce(n.fg_made_50_59, 0) + coalesce(n.fg_made_60_, 0)) as fg_made_50,
           sum(coalesce(n.pat_made, 0))               as pat_made,
           sum(coalesce(tw.carries, 0))               as team_carries,   -- in the games he played
           sum(coalesce(tw.targets, 0))               as team_targets
    from core.nfl_player_week n
    join league_weeks lw on lw.season = n.season and n.week <= lw.last_week
    left join team_week tw on tw.team = n.team and tw.season = n.season and tw.week = n.week
    where n.season_type = 'REG'
    group by n.gsis_id, n.season
),
usage as materialized (
    select u.gsis_id, u.season,
           sum(u.targets_rz + u.carries_rz)   as red_zone_looks,
           sum(u.targets_ez)                  as end_zone_targets,
           sum(u.targets_deep)                as deep_targets,
           sum(u.carries_gl)                  as goal_line_carries
    from core.nfl_usage_week u
    join league_weeks lw on lw.season = u.season and u.week <= lw.last_week
    group by u.gsis_id, u.season
),
snaps as materialized (
    select s.gsis_id, s.season, round(avg(s.offense_pct), 3) as snap_share
    from core.nfl_snap_week s
    join league_weeks lw on lw.season = s.season and s.week <= lw.last_week
    where s.offense_snaps > 0
    group by s.gsis_id, s.season
),
expected as materialized (
    select e.gsis_id, e.season, sum(e.expected_points) as expected_points, sum(e.actual_points) as actual_points
    from core.v_expected_league_points e
    join league_weeks lw on lw.season = e.season and e.week <= lw.last_week
    group by e.gsis_id, e.season
),
defense as materialized (
    select d.sleeper_def_id, d.season,
           sum(d.sacks)                                         as def_sacks,
           sum(d.interceptions)                                 as def_interceptions,
           sum(d.fumble_recoveries + d.st_fumble_recoveries)    as def_fumble_recoveries,
           sum(d.defensive_tds + d.return_tds)                  as def_tds,
           sum(d.points_allowed)                                as def_points_allowed,
           count(d.points_allowed)                              as def_pa_games,
           round(avg(d.points_allowed), 1)                      as def_points_allowed_pg,
           round(avg(d.yards_allowed), 1)                       as def_yards_allowed_pg
    from core.v_def_league_points d
    join league_weeks lw on lw.season = d.season and d.week <= lw.last_week
    where d.sleeper_def_id is not null
    group by d.sleeper_def_id, d.season
),
league_team as materialized (             -- league rosters: each team that had him that season
    select season, player_id, franchise_code, min(week) as first_week, count(*) as weeks,
           count(*) filter (where is_starter) as starts,
           coalesce(sum(points) filter (where is_starter), 0) as started_points
    from core.player_week
    group by season, player_id, franchise_code
),
league as materialized (
    select season, player_id,
           string_agg(franchise_code, ',' order by first_week, franchise_code) as league_teams,
           sum(weeks)          as league_weeks,
           sum(starts)         as league_starts,
           sum(started_points) as league_started_points
    from league_team
    group by season, player_id
),
season_rows as (
    select
        f.season, f.rank_id, f.gsis_id, f.player_id, f.player_name, f.position, f.nfl_team,
        f.games,
        least(6, ceil(sw.weeks / 2.0))::int                                       as min_games,
        f.games >= least(6, ceil(sw.weeks / 2.0))                                 as qualified,
        round(f.points, 2)                                                        as points,
        round(f.points / f.games, 2)                                              as points_per_game,
        round(f.points - f.replacement_per_week * sw.weeks, 2)                    as points_over_replacement,
        f.best_week, f.best_week_rank, f.top1_weeks, f.starter_weeks, f.starter_line,
        round(f.starter_weeks::numeric / f.games, 3)                              as consistency,
        f.boom_weeks, f.bust_weeks,
        coalesce(l.league_weeks, 0)                                               as league_weeks,
        coalesce(l.league_starts, 0)                                              as league_starts,
        coalesce(l.league_started_points, 0)                                      as league_started_points,
        l.league_teams,
        rs.completions, rs.pass_attempts, rs.passing_yards, rs.passing_tds, rs.interceptions,
        rs.carries, rs.rushing_yards, rs.rushing_tds,
        rs.targets, rs.receptions, rs.receiving_yards, rs.receiving_tds,
        rs.fumbles_lost, rs.fg_made, rs.fg_att, rs.fg_long, rs.fg_made_50, rs.pat_made,
        rs.team_carries, rs.team_targets,                                          -- his teams' totals in his games
        round(rs.carries::numeric / nullif(rs.team_carries, 0), 3)                as carry_share,
        round(rs.targets::numeric / nullif(rs.team_targets, 0), 3)                as target_share,
        u.red_zone_looks, u.end_zone_targets, u.deep_targets, u.goal_line_carries,
        sn.snap_share,
        round(e.expected_points, 2)                                               as expected_points,
        round(e.actual_points - e.expected_points, 2)                             as points_over_expected,
        d.def_sacks, d.def_interceptions, d.def_fumble_recoveries, d.def_tds,
        d.def_points_allowed, d.def_pa_games, d.def_points_allowed_pg, d.def_yards_allowed_pg,
        sw.weeks                                                                  as season_weeks
    from fantasy f
    join season_weeks sw on sw.season = f.season
    left join real_stats rs on rs.gsis_id = f.gsis_id and rs.season = f.season
    left join usage u       on u.gsis_id = f.gsis_id and u.season = f.season
    left join snaps sn      on sn.gsis_id = f.gsis_id and sn.season = f.season
    left join expected e    on e.gsis_id = f.gsis_id and e.season = f.season
    left join defense d     on d.sleeper_def_id = f.rank_id and d.season = f.season and f.position = 'DEF'
    left join league l      on l.season = f.season and l.player_id = f.player_id
)
select
    s.*,
    -- season finish: total points among every NFL player at his position
    rank()   over (partition by s.season, s.position order by s.points desc)                       as pos_rank,
    count(*) over (partition by s.season, s.position, s.points) > 1                               as pos_tied,
    count(*) over (partition by s.season, s.position)                                             as pos_count,
    rank()   over (partition by s.season order by s.points desc)                                  as overall_rank,
    -- per game: only players with the minimum games
    case when s.qualified then rank() over (partition by s.season, s.position, s.qualified
                                            order by s.points_per_game desc) end                  as pos_rank_ppg,
    case when s.qualified then count(*) over (partition by s.season, s.position, s.qualified,
                                              s.points_per_game) > 1 end                         as pos_tied_ppg,
    case when s.qualified then count(*) over (partition by s.season, s.position, s.qualified) end  as pos_count_ppg
from season_rows s;

comment on view core.v_player_season_rank is
    'Phase 12: every NFL player-season (2021 ->) over the league''s season (weeks 1 through the last playoff week): '
    'league points, per game, over replacement, starter / boom / bust weeks, real stats, usage, expected points, '
    'league rosters, and ranks (pos_rank = season finish by total points; pos_rank_ppg among players with min_games).';


-- ---------------------------------------------------------------------
-- All-time (2021 on): the season rows added up. Per-game ranks need 17 games.
-- ---------------------------------------------------------------------
create or replace view core.v_player_career_rank as
with careers as (
    select
        s.rank_id,
        max(s.gsis_id)                                                  as gsis_id,
        max(s.player_id)                                                as player_id,
        (array_agg(s.player_name order by s.season desc))[1]            as player_name,
        (array_agg(s.position order by s.season desc))[1]               as position,
        (array_agg(s.nfl_team order by s.season desc))[1]               as nfl_team,
        count(*)                                                        as seasons,
        min(s.season)                                                   as first_season,
        max(s.season)                                                   as last_season,
        sum(s.games)                                                    as games,
        17                                                              as min_games,
        round(sum(s.points), 2)                                         as points,
        round(sum(s.points) / sum(s.games), 2)                          as points_per_game,
        round(sum(s.points_over_replacement), 2)                        as points_over_replacement,
        max(s.best_week)                                                as best_week,
        min(s.best_week_rank)                                           as best_week_rank,
        sum(s.top1_weeks)                                               as top1_weeks,
        sum(s.starter_weeks)                                            as starter_weeks,
        round(sum(s.starter_weeks)::numeric / sum(s.games), 3)          as consistency,
        sum(s.boom_weeks)                                               as boom_weeks,
        sum(s.bust_weeks)                                               as bust_weeks,
        count(*) filter (where s.league_weeks > 0)                      as league_seasons,
        sum(s.league_weeks)                                             as league_weeks,
        sum(s.league_starts)                                            as league_starts,
        sum(s.league_started_points)                                    as league_started_points,
        sum(s.completions) as completions, sum(s.pass_attempts) as pass_attempts,
        sum(s.passing_yards) as passing_yards, sum(s.passing_tds) as passing_tds, sum(s.interceptions) as interceptions,
        sum(s.carries) as carries, sum(s.rushing_yards) as rushing_yards, sum(s.rushing_tds) as rushing_tds,
        sum(s.targets) as targets, sum(s.receptions) as receptions, sum(s.receiving_yards) as receiving_yards,
        sum(s.receiving_tds) as receiving_tds, sum(s.fumbles_lost) as fumbles_lost,
        sum(s.fg_made) as fg_made, sum(s.fg_att) as fg_att, max(s.fg_long) as fg_long,
        sum(s.fg_made_50) as fg_made_50, sum(s.pat_made) as pat_made,
        sum(s.team_carries) as team_carries, sum(s.team_targets) as team_targets,
        round(sum(s.carries)::numeric / nullif(sum(s.team_carries), 0), 3) as carry_share,
        round(sum(s.targets)::numeric / nullif(sum(s.team_targets), 0), 3) as target_share,
        sum(s.red_zone_looks) as red_zone_looks, sum(s.end_zone_targets) as end_zone_targets,
        sum(s.deep_targets) as deep_targets, sum(s.goal_line_carries) as goal_line_carries,
        round(sum(s.snap_share * s.games) / nullif(sum(s.games) filter (where s.snap_share is not null), 0), 3)
                                                                        as snap_share,   -- weighted by games
        round(sum(s.expected_points), 2)                                as expected_points,
        round(sum(s.points_over_expected), 2)                           as points_over_expected,
        sum(s.def_sacks) as def_sacks, sum(s.def_interceptions) as def_interceptions,
        sum(s.def_fumble_recoveries) as def_fumble_recoveries, sum(s.def_tds) as def_tds,
        round(sum(s.def_points_allowed)::numeric / nullif(sum(s.def_pa_games), 0), 1) as def_points_allowed_pg
    from core.v_player_season_rank s
    group by s.rank_id
),
league_teams as (            -- every league team that had him, in order of first arrival
    select player_id, string_agg(franchise_code, ',' order by first_at, franchise_code) as league_teams
    from (select player_id, franchise_code, min(season * 100 + week) as first_at
          from core.player_week
          group by player_id, franchise_code) t
    group by player_id
)
select
    c.*,
    c.games >= c.min_games                                                                         as qualified,
    lt.league_teams,
    rank()   over (partition by c.position order by c.points desc)                                 as pos_rank,
    count(*) over (partition by c.position, c.points) > 1                                          as pos_tied,
    count(*) over (partition by c.position)                                                        as pos_count,
    rank()   over (order by c.points desc)                                                         as overall_rank,
    case when c.games >= c.min_games then rank() over (partition by c.position, c.games >= c.min_games
                                                       order by c.points_per_game desc) end        as pos_rank_ppg,
    case when c.games >= c.min_games then count(*) over (partition by c.position, c.games >= c.min_games,
                                                         c.points_per_game) > 1 end               as pos_tied_ppg,
    case when c.games >= c.min_games then count(*) over (partition by c.position,
                                                         c.games >= c.min_games) end              as pos_count_ppg
from careers c
left join league_teams lt on lt.player_id = c.player_id;

comment on view core.v_player_career_rank is
    'Phase 12: all-time (2021 ->) totals per NFL player from core.v_player_season_rank, with all-time position ranks '
    '(per-game ranks need 17 games). position / nfl_team = his latest season''s.';


-- ---------------------------------------------------------------------
-- Positions page: each league team's position groups ranked 1-12 each season (regular season,
-- starters' points per week, from core.v_position_strength), plus the starter who led the group.
-- ---------------------------------------------------------------------
create or replace view core.v_position_group_rank as
with started as (
    select pw.season, pw.franchise_code, p.position, pw.player_id,
           count(*) as starts, sum(pw.points) as points
    from core.player_week pw
    join core.player p on p.player_id = pw.player_id
    join core.season s on s.season = pw.season
    where pw.is_starter and pw.week < s.playoff_week_start
    group by pw.season, pw.franchise_code, p.position, pw.player_id
),
lead as (
    select distinct on (st.season, st.franchise_code, st.position)
           st.season, st.franchise_code, st.position, st.player_id, st.starts, st.points
    from started st
    order by st.season, st.franchise_code, st.position, st.points desc, st.player_id
)
select
    ps.season, ps.franchise_code, ps.position,
    ps.started_points, ps.points_per_week, ps.league_average, ps.vs_league,
    rank()   over (partition by ps.season, ps.position order by ps.points_per_week desc)          as group_rank,
    count(*) over (partition by ps.season, ps.position, ps.points_per_week) > 1                   as group_tied,
    count(*) over (partition by ps.season, ps.position)                                           as teams,
    l.player_id                                                                                   as lead_player_id,
    coalesce(p.full_name, nullif(trim(coalesce(p.first_name, '') || ' ' || coalesce(p.last_name, '')), ''))
                                                                                                  as lead_player_name,
    l.starts                                                                                      as lead_starts,
    round(l.points, 2)                                                                            as lead_points
from core.v_position_strength ps
left join lead l on l.season = ps.season and l.franchise_code = ps.franchise_code and l.position = ps.position
left join core.player p on p.player_id = l.player_id
where ps.position <> '?';

comment on view core.v_position_group_rank is
    'Phase 12: each league team''s position group ranked 1-12 per season by starters'' points per regular-season week '
    '(core.v_position_strength), with the starter who scored the most for that group.';


-- ---------------------------------------------------------------------
-- Precomputed copies (Phase 11 rules): pages filter the weekly ranks by player or by week, which
-- otherwise computes every NFL player-week; the season and all-time views add everything up.
-- ---------------------------------------------------------------------
insert into core.precomputed (view_name, copy_name, index_columns, sort_order) values
    ('core.v_player_week_rank',    'core.copy_player_week_rank',    '{player_id,"season, week"}', 110),
    ('core.v_player_season_rank',  'core.copy_player_season_rank',  '{season,player_id}',         120),
    ('core.v_player_career_rank',  'core.copy_player_career_rank',  '{}',                         130)
on conflict (view_name) do update
    set copy_name = excluded.copy_name, index_columns = excluded.index_columns, sort_order = excluded.sort_order;
