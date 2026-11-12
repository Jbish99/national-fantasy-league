-- =====================================================================
-- db/checks/001_validation.sql
-- Validation checks as views in a "qa" schema.
-- Rule: every check returns ZERO rows when things are right.
-- Any row that shows up is a problem to look at.
--
-- Quick look at everything:   select * from qa.summary;
-- Not a problem list, just useful: qa.official_vs_sleeper_scores
-- Safe to re-run (CREATE OR REPLACE).
-- =====================================================================

create schema if not exists qa;
comment on schema qa is 'Validation checks. Each view returns only problem rows (except the *_info / official_vs_sleeper_scores views).';


-- ---------------------------------------------------------------------
-- A. Sleeper load checks: is Sleeper's data internally consistent?
-- ---------------------------------------------------------------------

-- 1. Each team's weekly Sleeper score equals the sum of its starters' points.
create or replace view qa.score_vs_starters as
select
    tw.season,
    tw.week,
    tw.franchise_code,
    tw.points                                                     as sleeper_points,
    coalesce(sum(pw.points) filter (where pw.is_starter), 0)      as starter_sum,
    tw.points - coalesce(sum(pw.points) filter (where pw.is_starter), 0) as diff
from core.team_week tw
left join core.player_week pw
       on pw.season = tw.season
      and pw.week = tw.week
      and pw.franchise_code = tw.franchise_code
where tw.source = 'sleeper'
group by tw.season, tw.week, tw.franchise_code, tw.points
having abs(tw.points - coalesce(sum(pw.points) filter (where pw.is_starter), 0)) >= 0.01;

comment on view qa.score_vs_starters is 'Sleeper team score does not equal the sum of its starters.';


-- 2. Every non-null Sleeper matchup_id has exactly two teams.
create or replace view qa.matchup_pairs as
select season, week, matchup_id, count(*) as teams
from core.team_week
where matchup_id is not null
group by season, week, matchup_id
having count(*) <> 2;

comment on view qa.matchup_pairs is 'A Sleeper matchup_id that does not have exactly two teams.';


-- 3. Season W/L/T and points computed from Sleeper's weekly scores match
--    Sleeper's own /rosters totals (completed Sleeper seasons).
--    2023 is skipped on purpose: Sleeper's 2023 /rosters season totals don't match
--    its own weekly scores (found 2026-11-12). The weekly scores match the workbook
--    for 192 of 196 team-weeks, so the weekly numbers are the trustworthy ones.
create or replace view qa.season_totals_vs_sleeper as
select
    fs.season,
    fs.franchise_code,
    r.wins,            fs.reported_wins,
    r.losses,          fs.reported_losses,
    r.ties,            fs.reported_ties,
    r.points_for,      fs.reported_points_for,
    r.points_against,  fs.reported_points_against
from core.franchise_season fs
join core.season s
  on s.season = fs.season
left join core.v_sleeper_season_record r
       on r.season = fs.season
      and r.franchise_code = fs.franchise_code
where s.platform = 'sleeper'
  and s.status = 'complete'
  and s.season <> 2023
  and fs.reported_wins is not null
  and (   r.wins   is distinct from fs.reported_wins
       or r.losses is distinct from fs.reported_losses
       or r.ties   is distinct from fs.reported_ties
       or abs(coalesce(r.points_for, 0)     - coalesce(fs.reported_points_for, 0))     >= 0.01
       or abs(coalesce(r.points_against, 0) - coalesce(fs.reported_points_against, 0)) >= 0.01);

comment on view qa.season_totals_vs_sleeper is 'Sleeper weekly scores don''t add up to Sleeper''s own season totals.';


-- 4. Each loaded Sleeper week has a row for every team.
create or replace view qa.teams_per_week as
select tw.season, tw.week, s.num_teams, count(*) as team_rows
from core.team_week tw
join core.season s on s.season = tw.season
group by tw.season, tw.week, s.num_teams
having count(*) <> s.num_teams;

comment on view qa.teams_per_week is 'A loaded Sleeper week is missing teams (or has extras).';


-- 5. Starters per team-week = number of starting slots minus empty slots.
create or replace view qa.starter_count as
with slots as (
    select season, count(*) as starting_slots
    from core.roster_slot
    where slot <> 'BN'
    group by season
)
select
    tw.season,
    tw.week,
    tw.franchise_code,
    count(pw.player_id) filter (where pw.is_starter) as starters,
    sl.starting_slots - tw.empty_starter_slots        as expected
from core.team_week tw
join slots sl on sl.season = tw.season
left join core.player_week pw
       on pw.season = tw.season
      and pw.week = tw.week
      and pw.franchise_code = tw.franchise_code
where tw.source = 'sleeper'
group by tw.season, tw.week, tw.franchise_code, sl.starting_slots, tw.empty_starter_slots
having count(pw.player_id) filter (where pw.is_starter) <> sl.starting_slots - tw.empty_starter_slots;

comment on view qa.starter_count is 'Wrong number of starters for the season''s lineup.';


-- 6. Sleeper's commissioner score override was used. Review these.
create or replace view qa.custom_points_used as
select season, week, franchise_code, points, custom_points
from core.team_week
where custom_points is not null;

comment on view qa.custom_points_used is 'Sleeper custom_points is set. Review which score is official.';


-- 7. Players that are only stubs (not found in /players/nfl).
create or replace view qa.stub_players as
select player_id, full_name, position
from core.player
where is_stub;

comment on view qa.stub_players is 'Player ids seen in league data but missing from /players/nfl.';


-- ---------------------------------------------------------------------
-- B. Official record checks: is core.game complete and sensible?
-- ---------------------------------------------------------------------

-- 8. Each season has one franchise_season row per team.
create or replace view qa.teams_per_season as
select s.season, s.num_teams, count(fs.franchise_code) as franchise_rows
from core.season s
left join core.franchise_season fs on fs.season = s.season
where s.num_teams is not null
group by s.season, s.num_teams
having count(fs.franchise_code) <> s.num_teams;

comment on view qa.teams_per_season is 'Season does not have one franchise row per team.';


-- 9. No team plays twice in the same week.
create or replace view qa.team_twice_in_week as
select season, week, franchise_code, count(*) as games
from core.v_team_game
group by season, week, franchise_code
having count(*) > 1;

comment on view qa.team_twice_in_week is 'A franchise has more than one official game in a week.';


-- 10. Every team in a season played the same number of regular-season games.
create or replace view qa.regular_season_game_count as
with counts as (
    select season, franchise_code, count(*) as games
    from core.v_team_game
    where game_type = 'regular'
    group by season, franchise_code
)
select c.season, c.franchise_code, c.games,
       (select max(games) from counts m where m.season = c.season) as most_games
from counts c
where c.games <> (select max(games) from counts m where m.season = c.season);

comment on view qa.regular_season_game_count is 'A franchise played fewer regular-season games than others that season.';


-- 11. Each season that has reached the postseason has exactly one Fantasy Bowl.
create or replace view qa.fantasy_bowl_count as
select s.season, count(g.game_id) as fantasy_bowls
from core.season s
left join core.game g on g.season = s.season and g.designation = 'Fantasy Bowl'
where s.season in (select distinct season from core.game where game_type <> 'regular')
group by s.season
having count(g.game_id) <> 1;

comment on view qa.fantasy_bowl_count is 'A season with postseason games but not exactly one Fantasy Bowl.';


-- 12. Overrides in the seed file that don't match any loaded game (typo in a Game ID?).
create or replace view qa.orphan_game_overrides as
select o.*
from core.game_override o
where not exists (select 1 from core.game g where g.game_id = o.game_id)
  and exists (select 1 from core.game g
              where g.season = split_part(o.game_id, '-', 1)::smallint);

comment on view qa.orphan_game_overrides is 'A game_override row whose Game ID isn''t in core.game.';


-- ---------------------------------------------------------------------
-- C. Official record vs Sleeper
-- ---------------------------------------------------------------------

-- 13. The workbook and Sleeper disagree on who played whom (Sleeper seasons, loaded weeks only).
create or replace view qa.pairing_vs_sleeper as
select g.game_id, g.season, g.week, g.team1_code, g.team2_code,
       t1.matchup_id as team1_sleeper_matchup, t2.matchup_id as team2_sleeper_matchup
from core.game g
join core.season s on s.season = g.season
left join core.team_week t1
       on t1.season = g.season and t1.week = g.week and t1.franchise_code = g.team1_code
left join core.team_week t2
       on t2.season = g.season and t2.week = g.week and t2.franchise_code = g.team2_code
where s.platform = 'sleeper'
  and exists (select 1 from core.team_week x where x.season = g.season and x.week = g.week)
  and (t1.matchup_id is null or t2.matchup_id is null or t1.matchup_id <> t2.matchup_id);

comment on view qa.pairing_vs_sleeper is 'An official game whose two teams did not share a Sleeper matchup that week.';


-- 14. In a season where Sleeper scores are official, the workbook names a different
--     winner than Sleeper and there's no override explaining it.
create or replace view qa.unexplained_winner_differences as
select g.game_id, g.season, g.week, g.team1_code, g.team2_code,
       g.team1_points, g.team2_points, g.winner_code as official_winner,
       case when t1.points > t2.points then g.team1_code
            when t2.points > t1.points then g.team2_code end as sleeper_winner,
       t1.points as team1_sleeper_points, t2.points as team2_sleeper_points
from core.game g
join core.season s on s.season = g.season
join core.team_week t1 on t1.season = g.season and t1.week = g.week and t1.franchise_code = g.team1_code
join core.team_week t2 on t2.season = g.season and t2.week = g.week and t2.franchise_code = g.team2_code
where s.official_scores_from = 'sleeper'
  and not exists (select 1 from core.game_override o where o.game_id = g.game_id)
  and g.winner_code is distinct from
      case when t1.points > t2.points then g.team1_code
           when t2.points > t1.points then g.team2_code end;

comment on view qa.unexplained_winner_differences is 'Workbook and Sleeper disagree on the winner, in a Sleeper-scored season, with no override. Decide which is right.';


-- Not a problem list: every game where the official score differs from Sleeper's.
-- Expected rows: all of 2023 (workbook is official) plus small stat-correction gaps.
create or replace view qa.official_vs_sleeper_scores as
select g.game_id, g.season, g.week, g.points_source,
       g.team1_code, g.team1_points, t1.points as team1_sleeper_points, g.team1_points - t1.points as team1_diff,
       g.team2_code, g.team2_points, t2.points as team2_sleeper_points, g.team2_points - t2.points as team2_diff
from core.game g
join core.team_week t1 on t1.season = g.season and t1.week = g.week and t1.franchise_code = g.team1_code
join core.team_week t2 on t2.season = g.season and t2.week = g.week and t2.franchise_code = g.team2_code
where abs(coalesce(g.team1_points - t1.points, 0)) >= 0.01
   or abs(coalesce(g.team2_points - t2.points, 0)) >= 0.01;

comment on view qa.official_vs_sleeper_scores is 'Info, not errors: games where the official score differs from Sleeper''s.';


-- ---------------------------------------------------------------------
-- Summary: one row per check with its problem count. All zeros = healthy.
-- ---------------------------------------------------------------------
create or replace view qa.summary as
          select 'score_vs_starters'             as check_name, count(*) as problems from qa.score_vs_starters
union all select 'matchup_pairs',                       count(*) from qa.matchup_pairs
union all select 'season_totals_vs_sleeper',            count(*) from qa.season_totals_vs_sleeper
union all select 'teams_per_week',                      count(*) from qa.teams_per_week
union all select 'starter_count',                       count(*) from qa.starter_count
union all select 'custom_points_used',                  count(*) from qa.custom_points_used
union all select 'stub_players',                        count(*) from qa.stub_players
union all select 'teams_per_season',                    count(*) from qa.teams_per_season
union all select 'team_twice_in_week',                  count(*) from qa.team_twice_in_week
union all select 'regular_season_game_count',           count(*) from qa.regular_season_game_count
union all select 'fantasy_bowl_count',                  count(*) from qa.fantasy_bowl_count
union all select 'orphan_game_overrides',               count(*) from qa.orphan_game_overrides
union all select 'pairing_vs_sleeper',                  count(*) from qa.pairing_vs_sleeper
union all select 'unexplained_winner_differences',      count(*) from qa.unexplained_winner_differences;

comment on view qa.summary is 'Problem count for every check. All zeros means the history is consistent.';
