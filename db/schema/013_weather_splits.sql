-- =====================================================================
-- db/schema/013_weather_splits.sql
-- Phase 7a: weather splits. How players and positions score in a dome, in mild
-- weather, in the cold and in the wind.
--
-- Conditions per NFL game (from nflverse schedules, core.nfl_game):
--   Dome         roof = dome, or a retractable roof that was closed
--   Cold         outdoors (or roof open) and 40°F or colder
--   Windy        outdoors (or roof open) and wind 15 mph or more
--   Mild         outdoors (or roof open), above 40°F and under 15 mph
--   Not on file  outdoors (or roof open) but the source has no temperature/wind
--                (temperature and wind are always missing together)
-- Cold and Windy overlap: a cold, windy game counts in both. Dome, Mild and
-- Not on file never overlap with anything.
--
-- Points: every NFL regular-season stat line from 2021 on, scored with our
-- league's rules for that season (core.v_nfl_league_points, 98.7-99.8% equal to
-- Sleeper's), whether or not the player was on a league roster that week.
-- vs_season_avg = that game's points minus the player's average over all his
-- games that season. Averaging that difference by condition takes out the
-- player's role and the year's scoring rules (full PPR 2021-22, half PPR after),
-- so it shows the weather effect better than raw points do.
-- Defenses aren't here yet: their fantasy points get rebuilt in Phase 7b.
--
-- Run after 012. Safe to re-run.
-- =====================================================================

create or replace view core.v_nfl_game_conditions as
select
    g.game_id,
    g.season,
    g.week,
    g.roof,
    g.temp,
    g.wind,
    (g.roof in ('dome', 'closed'))                                                    as is_dome,
    (g.roof not in ('dome', 'closed') and g.temp <= 40)                               as is_cold,
    (g.roof not in ('dome', 'closed') and g.wind >= 15)                               as is_windy,
    (g.roof not in ('dome', 'closed') and g.temp > 40 and g.wind < 15)                as is_mild,
    (g.roof is null or (g.roof not in ('dome', 'closed') and (g.temp is null or g.wind is null)))
                                                                                      as weather_not_on_file
from core.nfl_game g;

comment on view core.v_nfl_game_conditions is
    'Each NFL game''s conditions: dome, cold (40°F or below), windy (15+ mph), mild, or weather not on file.';


create or replace view core.v_player_week_conditions as
select
    lp.gsis_id,
    lp.season,
    lp.week,
    lp.player_name,
    lp.position                                                             as nfl_position,
    lp.team                                                                 as nfl_team,
    n.game_id                                                               as nfl_game_id,
    lp.league_points,
    count(*) over w                                                         as games_that_season,
    round(avg(lp.league_points) over w, 2)                                  as season_avg,
    round(lp.league_points - avg(lp.league_points) over w, 2)               as vs_season_avg,
    c.temp,
    c.wind,
    c.is_dome, c.is_cold, c.is_windy, c.is_mild, c.weather_not_on_file
from core.v_nfl_league_points lp
join core.nfl_player_week n
  on n.gsis_id = lp.gsis_id and n.season = lp.season and n.week = lp.week and n.season_type = lp.season_type
join core.v_nfl_game_conditions c on c.game_id = n.game_id
where lp.season_type = 'REG'
  and lp.season >= 2021
window w as (partition by lp.gsis_id, lp.season);

comment on view core.v_player_week_conditions is
    'Every NFL regular-season player-week (2021 ->) scored with league rules, vs his season average, with the game''s conditions.';


-- Position splits for players who mattered in our league: anyone in a league starting
-- lineup at least once that season (his whole season counts, rostered or not).
-- season is null on the all-seasons rows (grouping sets). Sum columns let anything be re-added.
create or replace view core.v_position_conditions as
with league_players as (
    select distinct on (pw.season, x.gsis_id) pw.season, x.gsis_id, p.position
    from core.player_week pw
    join core.player_xref x on x.player_id = pw.player_id
    join core.player p      on p.player_id = pw.player_id
    where pw.is_starter
      and p.position in ('QB', 'RB', 'WR', 'TE', 'K')
    order by pw.season, x.gsis_id, p.position
),
by_condition as (
    select lp.position, w.season, b.condition, b.sort_order, w.league_points, w.vs_season_avg
    from core.v_player_week_conditions w
    join league_players lp on lp.season = w.season and lp.gsis_id = w.gsis_id
    cross join lateral (values ('Dome', 1, w.is_dome), ('Mild', 2, w.is_mild), ('Cold', 3, w.is_cold),
                               ('Windy', 4, w.is_windy), ('Not on file', 5, w.weather_not_on_file))
                       as b(condition, sort_order, hit)
    where b.hit
)
select
    season,                                                                 -- null = all seasons
    position,
    condition,
    sort_order,
    count(*)                                                                as games,
    round(avg(league_points), 2)                                            as points_per_game,
    round(avg(vs_season_avg), 2)                                            as vs_season_avg,
    sum(league_points)                                                      as sum_points,
    sum(vs_season_avg)                                                      as sum_vs_season_avg
from by_condition
group by grouping sets ((season, position, condition, sort_order), (position, condition, sort_order));

comment on view core.v_position_conditions is
    'Points per game and points vs the player''s season average, by position and weather, for players started in the league that season.';
