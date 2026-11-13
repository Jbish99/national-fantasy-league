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
--    W/L/T must match exactly. Points may differ by up to 3: Sleeper's season totals
--    don't always pick up late stat corrections (2021 and 2024 have 0.01-2.00 gaps where
--    the weekly score and the workbook agree). A real load problem is off by far more.
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
       or abs(coalesce(r.points_for, 0)     - coalesce(fs.reported_points_for, 0))     > 3
       or abs(coalesce(r.points_against, 0) - coalesce(fs.reported_points_against, 0)) > 3);

comment on view qa.season_totals_vs_sleeper is
    'Sleeper weekly scores don''t match Sleeper''s own season totals (exact W/L/T; points within 3). 2023 skipped.';


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


-- ---------------------------------------------------------------------
-- D. Workbook player names (keepers, 2020 draft, awards)
-- ---------------------------------------------------------------------

-- 15. Workbook names with no Sleeper player yet. Fix in data/manual/player_name_map.csv
--     (fill player_id + status confirmed, or status not_a_player), then re-run load_core.
create or replace view qa.unmatched_names as
select source, season, team_code, name_as_entered, position, candidates
from core.player_name_map
where status = 'unmatched';

comment on view qa.unmatched_names is 'Workbook player names that still need a Sleeper player id.';


-- 16. Every keeper in the workbook made it into core.keeper.
create or replace view qa.keepers_not_loaded as
with workbook as (
    select (data ->> 'Year')::smallint as season, count(*) as workbook_keepers
    from raw.workbook_row
    where sheet = 'Keepers'
    group by 1
),
loaded as (
    select season, count(*) as loaded_keepers
    from core.keeper
    group by season
)
select w.season, w.workbook_keepers, coalesce(l.loaded_keepers, 0) as loaded_keepers
from workbook w
left join loaded l on l.season = w.season
where coalesce(l.loaded_keepers, 0) <> w.workbook_keepers;

comment on view qa.keepers_not_loaded is 'Seasons where fewer keepers loaded than the workbook lists (usually an unmatched name).';


-- ---------------------------------------------------------------------
-- E. NFL stats (Phase 3)
-- ---------------------------------------------------------------------

-- 17. Players who've been on a league roster but have no NFL id, so no real stat line.
--     Defenses are left out (their stat lines are team totals, not players).
--     Fix: add a row to data/manual/nfl_id_overrides.csv, then re-run load_nflverse.
create or replace view qa.league_players_without_nfl_id as
select p.player_id, p.full_name, p.position, min(pw.season) as first_season, max(pw.season) as last_season,
       sum(pw.points) as league_points
from core.player p
join core.player_week pw on pw.player_id = p.player_id
where p.position is distinct from 'DEF'
  and not exists (select 1 from core.player_xref x where x.player_id = p.player_id)
group by p.player_id, p.full_name, p.position;

comment on view qa.league_players_without_nfl_id is 'League-rostered players with no NFL (gsis) id, so no real stat line.';


-- 18. Two Sleeper ids pointing at the same NFL player (one of them is probably wrong).
create or replace view qa.nfl_id_shared as
select x.gsis_id, string_agg(x.player_id || ' ' || coalesce(p.full_name, '?') || ' (' || x.method || ')', '; ') as sleeper_players
from core.player_xref x
join core.player p on p.player_id = x.player_id
group by x.gsis_id
having count(*) > 1
   and count(*) filter (where exists (select 1 from core.player_week pw where pw.player_id = x.player_id)) > 0;

comment on view qa.nfl_id_shared is 'An NFL id linked to more than one Sleeper player, at least one of them rostered in the league.';


-- 19. Stat lines whose game isn't in core.nfl_game.
create or replace view qa.nfl_stats_without_game as
select s.season, s.week, s.game_id, count(*) as stat_rows
from core.nfl_player_week s
where s.game_id is null
   or not exists (select 1 from core.nfl_game g where g.game_id = s.game_id)
group by s.season, s.week, s.game_id;

comment on view qa.nfl_stats_without_game is 'Stat lines that don''t link to an NFL game (so no weather).';


-- 20. Scoring rules the league uses that can't be rebuilt from a weekly stat line
--     (needs play-by-play detail, e.g. points per 40+ yard catch). Defense rules are left out.
--     If this ever lists something, rebuilt points will be off by that rule.
create or replace view qa.scoring_not_covered as
select ss.season, ss.stat_key, ss.points
from core.scoring_setting ss
where ss.points <> 0
  and ss.stat_key not in (select stat_key from core.v_scoring_rules_covered)
  and ss.stat_key !~ '^(def_|idp_|pts_allow|yds_allow)'
  and ss.stat_key not in ('sack', 'sack_yd', 'int', 'int_ret_yd', 'ff', 'fum_rec', 'fum_ret_yd', 'safe', 'blk_kick',
                          'blk_kick_ret_yd', 'fg_ret_yd', 'tkl', 'tkl_solo', 'tkl_ast', 'tkl_loss', 'qb_hit',
                          'st_ff', 'st_fum_rec', 'st_tkl_solo', 'bonus_sack_2p', 'bonus_tkl_10p',
                          'bonus_def_int_td_50p', 'bonus_def_fum_td_50p');

comment on view qa.scoring_not_covered is 'Offensive/kicking scoring rules in use that rebuilt points can''t answer.';


-- Not a problem list: every rostered player-week, Sleeper's points next to the points rebuilt
-- from the real stat line. computed_points is null when there's no stat line that week.
create or replace view qa.league_points_vs_sleeper as
select pw.season, pw.week, pw.franchise_code, pw.player_id, p.full_name, p.position, x.gsis_id,
       pw.points                               as sleeper_points,
       lp.league_points                        as computed_points,
       pw.points - lp.league_points            as diff
from core.player_week pw
join core.player p          on p.player_id = pw.player_id
left join core.player_xref x on x.player_id = pw.player_id
left join core.v_nfl_league_points lp
       on lp.gsis_id = x.gsis_id and lp.season = pw.season and lp.week = pw.week and lp.season_type = 'REG'
where p.position is distinct from 'DEF'
  and (lp.gsis_id is not null or pw.points <> 0);

comment on view qa.league_points_vs_sleeper is
    'Info: Sleeper points vs points rebuilt from the real stat line, per rostered player-week (defenses aside).';


-- Not a problem list: how often rebuilt points match Sleeper, by season.
create or replace view qa.league_points_accuracy as
select season,
       count(*) filter (where computed_points is not null)                          as compared,
       count(*) filter (where abs(diff) < 0.01)                                     as exact,
       count(*) filter (where abs(diff) >= 0.01 and abs(diff) <= 1)                 as within_1,
       count(*) filter (where abs(diff) > 1)                                        as off_by_more,
       count(*) filter (where computed_points is null)                              as points_but_no_stat_line,
       round(100.0 * count(*) filter (where abs(diff) < 0.01)
             / nullif(count(*) filter (where computed_points is not null), 0), 1)   as exact_pct
from qa.league_points_vs_sleeper
group by season
order by season;

comment on view qa.league_points_accuracy is 'Info: share of player-weeks where rebuilt points equal Sleeper''s, by season.';


-- 21. League players whose NFL id is probably wrong: most of their weeks don't match Sleeper,
--     or Sleeper gave them points in weeks their NFL id has no stat line.
create or replace view qa.nfl_id_suspect as
select player_id, full_name, position, gsis_id,
       count(*) filter (where computed_points is not null)                 as weeks_compared,
       count(*) filter (where abs(diff) < 0.01)                            as weeks_exact,
       count(*) filter (where computed_points is null)                     as weeks_points_but_no_stat_line,
       round(sum(sleeper_points), 1)                                       as sleeper_total,
       round(sum(computed_points), 1)                                      as computed_total
from qa.league_points_vs_sleeper
group by player_id, full_name, position, gsis_id
having (count(*) filter (where computed_points is not null) >= 3          -- most weeks disagree
        and count(*) filter (where abs(diff) < 0.01) * 2 < count(*) filter (where computed_points is not null))
    or (count(*) filter (where computed_points is not null) between 1 and 2  -- short stint, never matches
        and count(*) filter (where abs(diff) < 0.01) = 0
        and max(abs(diff)) > 1)
    or count(*) filter (where computed_points is null) >= 2;                 -- scored, but no stat line

comment on view qa.nfl_id_suspect is
    'League players whose rebuilt points mostly disagree with Sleeper (likely a wrong NFL id). Fix in data/manual/nfl_id_overrides.csv.';


-- Not a problem list: NFL id links that needed a decision (conflicts, duplicates, name matches).
create or replace view qa.nfl_id_notes as
select x.player_id, p.full_name, p.position, x.gsis_id, x.method, x.note
from core.player_xref x
join core.player p on p.player_id = x.player_id
where x.note is not null
  and exists (select 1 from core.player_week pw where pw.player_id = x.player_id)
order by x.method, p.full_name;

comment on view qa.nfl_id_notes is 'Info: league players whose NFL id link needed a decision. Worth a glance.';


-- Not a problem list: how much weather the source has, per season.
create or replace view qa.weather_coverage as
select season,
       count(*) filter (where home_score is not null)                                      as played_games,
       count(*) filter (where home_score is not null and roof in ('outdoors', 'open'))     as outdoor_played,
       count(*) filter (where home_score is not null and roof in ('outdoors', 'open') and temp is null) as outdoor_missing_temp,
       count(*) filter (where home_score is not null and roof in ('outdoors', 'open') and wind is null) as outdoor_missing_wind
from core.nfl_game
group by season
order by season;

comment on view qa.weather_coverage is 'Info: played outdoor/open-roof games missing temperature or wind in the source, by season.';


-- Not a problem list: close-spelling matches worth a quick look (status = check).
create or replace view qa.name_matches_to_check as
select source, season, team_code, name_as_entered, position, matched_name, player_id, score, candidates
from core.player_name_map
where status = 'check'
order by score, season;

comment on view qa.name_matches_to_check is 'Info: probable name matches (close spellings). Confirm or fix them in the CSV.';


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
union all select 'unexplained_winner_differences',      count(*) from qa.unexplained_winner_differences
union all select 'unmatched_names',                     count(*) from qa.unmatched_names
union all select 'keepers_not_loaded',                  count(*) from qa.keepers_not_loaded
union all select 'league_players_without_nfl_id',       count(*) from qa.league_players_without_nfl_id
union all select 'nfl_id_shared',                       count(*) from qa.nfl_id_shared
union all select 'nfl_stats_without_game',              count(*) from qa.nfl_stats_without_game
union all select 'scoring_not_covered',                 count(*) from qa.scoring_not_covered
union all select 'nfl_id_suspect',                      count(*) from qa.nfl_id_suspect;

comment on view qa.summary is 'Problem count for every check. All zeros means the history is consistent.';
