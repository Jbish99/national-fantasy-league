-- =====================================================================
-- db/checks/001_validation.sql
-- Validation checks as views in a "qa" schema.
-- Rule: every check returns ZERO rows when things are right.
-- Any row that shows up is a problem to look at.
--
-- Quick look at everything:   select * from qa.summary;
-- Safe to re-run (CREATE OR REPLACE).
-- =====================================================================

create schema if not exists qa;
comment on schema qa is 'Validation checks. Each view returns only problem rows.';


-- 1. Each team's weekly score equals the sum of its starters' points.
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

comment on view qa.score_vs_starters is 'Team score does not equal the sum of its starters.';


-- 2. Every non-null matchup_id has exactly two teams.
create or replace view qa.matchup_pairs as
select season, week, matchup_id, count(*) as teams
from core.team_week
where matchup_id is not null
group by season, week, matchup_id
having count(*) <> 2;

comment on view qa.matchup_pairs is 'A matchup_id that does not have exactly two teams.';


-- 3. Season W/L/T and points match Sleeper's /rosters totals (completed Sleeper seasons).
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
left join core.v_regular_season_record r
       on r.season = fs.season
      and r.franchise_code = fs.franchise_code
where s.platform = 'sleeper'
  and s.status = 'complete'
  and (   r.wins   is distinct from fs.reported_wins
       or r.losses is distinct from fs.reported_losses
       or r.ties   is distinct from fs.reported_ties
       or abs(coalesce(r.points_for, 0)     - coalesce(fs.reported_points_for, 0))     >= 0.01
       or abs(coalesce(r.points_against, 0) - coalesce(fs.reported_points_against, 0)) >= 0.01);

comment on view qa.season_totals_vs_sleeper is 'Our regular-season totals differ from what Sleeper reports.';


-- 4. Each season has one franchise_season row per team.
create or replace view qa.teams_per_season as
select s.season, s.num_teams, count(fs.franchise_code) as franchise_rows
from core.season s
left join core.franchise_season fs on fs.season = s.season
where s.num_teams is not null
group by s.season, s.num_teams
having count(fs.franchise_code) <> s.num_teams;

comment on view qa.teams_per_season is 'Season does not have one franchise row per team.';


-- 5. Each loaded week has a row for every team.
create or replace view qa.teams_per_week as
select tw.season, tw.week, s.num_teams, count(*) as team_rows
from core.team_week tw
join core.season s on s.season = tw.season
group by tw.season, tw.week, s.num_teams
having count(*) <> s.num_teams;

comment on view qa.teams_per_week is 'A loaded week is missing teams (or has extras).';


-- 6. Starters per team-week = number of starting slots minus empty slots.
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


-- 7. Sleeper's commissioner score override was used. Not an error by itself,
--    but we need to look and decide which score is official.
create or replace view qa.custom_points_used as
select season, week, franchise_code, points, custom_points
from core.team_week
where custom_points is not null;

comment on view qa.custom_points_used is 'Sleeper custom_points is set. Review and add a result_override if needed.';


-- 8. Players that are only stubs (not found in /players/nfl).
create or replace view qa.stub_players as
select player_id, full_name, position
from core.player
where is_stub;

comment on view qa.stub_players is 'Player ids seen in league data but missing from /players/nfl.';


-- Summary: one row per check with its problem count. All zeros = healthy.
create or replace view qa.summary as
          select 'score_vs_starters'        as check_name, count(*) as problems from qa.score_vs_starters
union all select 'matchup_pairs',                  count(*) from qa.matchup_pairs
union all select 'season_totals_vs_sleeper',       count(*) from qa.season_totals_vs_sleeper
union all select 'teams_per_season',               count(*) from qa.teams_per_season
union all select 'teams_per_week',                 count(*) from qa.teams_per_week
union all select 'starter_count',                  count(*) from qa.starter_count
union all select 'custom_points_used',             count(*) from qa.custom_points_used
union all select 'stub_players',                   count(*) from qa.stub_players;

comment on view qa.summary is 'Problem count for every check. All zeros means the history is consistent.';
