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


-- 11. Each finished season has exactly one Fantasy Bowl. A season still being played
--     (playoffs under way, final not in the workbook yet) only fails if it has more than one.
create or replace view qa.fantasy_bowl_count as
select s.season, count(g.game_id) as fantasy_bowls, s.status   -- new column last, so create-or-replace works
from core.season s
left join core.game g on g.season = s.season and g.designation = 'Fantasy Bowl'
where s.season in (select distinct season from core.game where game_type <> 'regular')
group by s.season, s.status
having count(g.game_id) > 1
    or (count(g.game_id) = 0 and coalesce(s.status, 'complete') = 'complete');

comment on view qa.fantasy_bowl_count is
    'A finished season without exactly one Fantasy Bowl, or any season with more than one.';


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
       pw.points - lp.league_points            as diff,
       case when pw.season = 2022 and pw.week = 17 and lp.gsis_id is null and pw.points <> 0
                 and exists (select 1 from core.nfl_player_week t      -- a Bills or Bengals player that month
                             where t.gsis_id = x.gsis_id and t.season = 2022
                               and t.week between 15 and 18 and t.team in ('BUF', 'CIN'))
            then 'Bills-Bengals game cancelled (Damar Hamlin): Sleeper kept stats from the part that was played; '
                 'the NFL''s official stats have none'
       end                                     as note
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
drop view if exists qa.league_points_accuracy;   -- info view; dropped so its columns can change
create view qa.league_points_accuracy as
select season,
       count(*) filter (where computed_points is not null)                          as compared,
       count(*) filter (where abs(diff) < 0.01)                                     as exact,
       count(*) filter (where abs(diff) >= 0.01 and abs(diff) <= 1)                 as within_1,
       count(*) filter (where abs(diff) > 1)                                        as off_by_more,
       count(*) filter (where computed_points is null and note is null)             as points_but_no_stat_line,
       count(*) filter (where note is not null)                                     as known_exceptions,
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
       count(*) filter (where computed_points is null and note is null)    as weeks_points_but_no_stat_line,
       round(sum(sleeper_points), 1)                                       as sleeper_total,
       round(sum(computed_points), 1)                                      as computed_total
from qa.league_points_vs_sleeper
group by player_id, full_name, position, gsis_id
having (count(*) filter (where computed_points is not null) >= 3          -- most weeks disagree
        and count(*) filter (where abs(diff) < 0.01) * 2 < count(*) filter (where computed_points is not null))
    or (count(*) filter (where computed_points is not null) between 1 and 2  -- short stint, never matches
        and count(*) filter (where abs(diff) < 0.01) = 0
        and max(abs(diff)) > 1)
    or count(*) filter (where computed_points is null and note is null) >= 2; -- scored, but no stat line

comment on view qa.nfl_id_suspect is
    'League players whose rebuilt points mostly disagree with Sleeper (likely a wrong NFL id). Fix in data/manual/nfl_id_overrides.csv.';


-- 22. Rostered Sleeper defenses that scored points in a week with no NFL team stat line
--     (a team-code mismatch, e.g. Sleeper LAR vs nflverse LA). The cancelled 2022 week 17
--     Bills-Bengals game has no official stats, so those two are left out.
create or replace view qa.league_defenses_without_stat_line as
select pw.season, pw.week, pw.franchise_code, pw.player_id as defense, pw.points
from core.player_week pw
join core.player p on p.player_id = pw.player_id and p.position = 'DEF'
where pw.points <> 0
  and not (pw.season = 2022 and pw.week = 17 and pw.player_id in ('BUF', 'CIN'))
  and not exists (select 1 from core.nfl_team_week t
                  where t.sleeper_def_id = pw.player_id and t.season = pw.season and t.week = pw.week
                    and t.season_type = 'REG');

comment on view qa.league_defenses_without_stat_line is 'Sleeper DEF player-weeks with points but no NFL team stat line.';


-- 23. Lineup slots the best-lineup calculation doesn't know (it handles QB RB WR TE FLEX K DEF BN).
create or replace view qa.unknown_lineup_slots as
select season, slot, count(*) as slots
from core.roster_slot
where slot not in ('QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DEF', 'BN')
group by season, slot;

comment on view qa.unknown_lineup_slots is 'Lineup slots (e.g. SUPER_FLEX) that core.v_lineup_players doesn''t handle yet.';


-- 24. Weeks where the "best possible lineup" scored less than the lineup actually started.
--     That can't happen if positions are right, so a row here means a player's position is
--     missing or unusual (the best-lineup numbers for that team-week are off).
create or replace view qa.best_lineup_below_actual as
select season, week, franchise_code, actual_points, best_points
from core.v_lineup_week
where best_points < actual_points - 0.01;

comment on view qa.best_lineup_below_actual is 'Team-weeks where the best-lineup calculation fell short of the real lineup.';


-- Phase 7a: the player game log (db/schema/012) must have exactly one row per rostered
-- player-week. A row here means one of its joins matched twice (e.g. two NFL team codes
-- pointing at the same Sleeper defense in one week), which would double a player on the
-- Player and Box score pages. Affects those pages only, so the refresh just warns.
create or replace view qa.player_log_duplicates as
select season, week, franchise_code, player_id, count(*) as log_rows
from core.v_player_game_log
group by season, week, franchise_code, player_id
having count(*) > 1;

comment on view qa.player_log_duplicates is 'Player-weeks that appear more than once in core.v_player_game_log.';


-- Phase 7b: team defense (DEF) points rebuilt from nflverse (core.v_def_league_points) vs what Sleeper
-- gave every rostered defense. Not a problem list: some gaps are expected (stat providers count passes
-- defended and tackles for loss a little differently; late stat corrections).
create or replace view qa.def_points_vs_sleeper as
select pw.season, pw.week, pw.franchise_code, pw.player_id as defense,
       pw.points                  as sleeper_points,
       d.league_points            as rebuilt_points,
       pw.points - d.league_points as diff
from core.player_week pw
join core.player p on p.player_id = pw.player_id and p.position = 'DEF'
left join core.v_def_league_points d on d.sleeper_def_id = pw.player_id and d.season = pw.season and d.week = pw.week
where not (pw.season = 2022 and pw.week = 17 and pw.player_id in ('BUF', 'CIN'));   -- cancelled game

comment on view qa.def_points_vs_sleeper is 'Info: Sleeper DEF points vs points rebuilt from nflverse, per rostered defense-week.';

drop view if exists qa.summary;                  -- depends on the next view; recreated at the end of this file
drop view if exists qa.def_points_accuracy_low;
drop view if exists qa.def_points_accuracy;      -- info view; dropped so its columns can change
create view qa.def_points_accuracy as
select season,
       count(*) filter (where rebuilt_points is not null)                                as compared,
       count(*) filter (where abs(diff) < 0.01)                                          as exact,
       count(*) filter (where abs(diff) >= 0.01 and abs(diff) <= 1)                      as within_1,
       count(*) filter (where abs(diff) > 1)                                             as off_by_more,
       count(*) filter (where rebuilt_points is null and sleeper_points <> 0)            as no_rebuild,
       round(100.0 * count(*) filter (where abs(diff) < 0.01)
             / nullif(count(*) filter (where rebuilt_points is not null), 0), 1)         as exact_pct
from qa.def_points_vs_sleeper
group by season
order by season;

comment on view qa.def_points_accuracy is 'Info: share of rostered defense-weeks where rebuilt DEF points equal Sleeper''s, by season.';

-- 25. A season where rebuilt DEF points match Sleeper on fewer than 90% of defense-weeks: a rule or a
--     play-by-play column probably changed. Affects DEF weather splits only, so the refresh just warns.
create view qa.def_points_accuracy_low as
select * from qa.def_points_accuracy where compared >= 50 and exact_pct < 90;

comment on view qa.def_points_accuracy_low is 'Seasons where rebuilt DEF points match Sleeper less than 90% of the time.';


-- Phase 9 (trade analyzer). All three only affect the Trade Analyzer page, so the refresh just warns.
-- A traded player's trade season must count exactly what core.v_acquisitions counts for that
-- trade (same weeks, same points). Should be empty.
create or replace view qa.trade_vs_acquisitions as
with first_season as (
    select asset_id, count(*) as weeks_rostered,
           coalesce(sum(points), 0)                         as points_rostered,
           coalesce(sum(points) filter (where is_starter), 0) as points_started
    from core.v_trade_asset_week
    where kept_seasons = 0
    group by asset_id
)
select a.transaction_id, a.season, a.week, a.receiver_code, a.player_id,
       acq.weeks_rostered as acquisitions_weeks,  coalesce(f.weeks_rostered, 0) as analyzer_weeks,
       acq.points_started as acquisitions_points, coalesce(f.points_started, 0) as analyzer_points
from core.v_trade_asset a
join core.v_acquisitions acq on acq.transaction_id = a.transaction_id and acq.franchise_code = a.receiver_code
                            and acq.player_id = a.player_id
left join first_season f on f.asset_id = a.asset_id
where a.asset_type = 'player'
  and (acq.weeks_rostered <> coalesce(f.weeks_rostered, 0)
       or acq.points_rostered <> coalesce(f.points_rostered, 0)
       or acq.points_started <> coalesce(f.points_started, 0));

comment on view qa.trade_vs_acquisitions is 'Traded players whose trade-season numbers differ from core.v_acquisitions.';


-- Traded picks from drafts that already happened that couldn't be tied to the player drafted with them
-- (no pick in that round + the original team's column, or the pick there was made by someone other
-- than the pick's final owner, e.g. a keeper placed in a traded round). Should be empty.
create or replace view qa.traded_pick_unmatched as
select pick_season, pick_round, original_code, final_owner_code, draft_slot, slot_source,
       pick_no, made_by_code, player_id, status
from core.v_traded_pick
where status in ('no_match', 'made_by_other');

comment on view qa.traded_pick_unmatched is 'Traded picks the Trade Analyzer could not match to a drafted player.';


-- The Trade Analyzer's replacement levels must equal the Draft page's. Should be empty.
create or replace view qa.replacement_level_mismatch as
select dv.season, dv.position, dv.replacement_rank as draft_rank, rl.replacement_rank as trade_rank,
       dv.replacement_points as draft_points, rl.replacement_points as trade_points
from (select distinct season, position, replacement_rank, replacement_points
      from core.v_draft_value where replacement_points is not null) dv
left join core.v_replacement_level rl on rl.season = dv.season and rl.position = dv.position
where rl.replacement_points is distinct from dv.replacement_points
   or rl.replacement_rank is distinct from dv.replacement_rank;

comment on view qa.replacement_level_mismatch is 'Seasons/positions where the trade and draft replacement levels differ.';


-- Phase 10 (team identities). Both only affect the Team identities page, so the refresh just warns.
-- The passing / rushing / receiving / kicking / other split must add up to the league points. Should be empty.
create or replace view qa.points_split_mismatch as
select sp.gsis_id, sp.season, sp.week, lp.league_points,
       round(sp.passing_points + sp.rushing_points + sp.receiving_points + sp.kicking_points + sp.other_points, 2)
           as split_total
from core.v_nfl_points_split sp
join core.v_nfl_league_points lp on lp.gsis_id = sp.gsis_id and lp.season = sp.season and lp.week = sp.week
where round(sp.passing_points + sp.rushing_points + sp.receiving_points + sp.kicking_points + sp.other_points, 2)
      <> lp.league_points;

comment on view qa.points_split_mismatch is 'Stat lines whose points split doesn''t add up to the league points.';


-- An identity label (db/seed/001) pointing at a measure core.v_team_metric doesn't have
-- (a typo when editing the labels). Should be empty.
create or replace view qa.identity_metric_unknown as
select i.identity_code, i.name, i.metric
from core.identity i
where i.metric is not null
  and not exists (select 1 from core.v_team_metric m where m.metric = i.metric)
  and exists (select 1 from core.v_team_profile);

comment on view qa.identity_metric_unknown is 'Identity labels whose metric doesn''t exist in core.v_team_metric.';


-- Phase 10b: a formation the FLEX rule produces (db/schema/017) with no row in core.formation, or no
-- fallback ("Multiple") row (a row deleted from db/seed/001?). Should be empty.
create or replace view qa.formation_unknown as
select distinct w.formation_code, 'no row in core.formation' as problem
from core.v_team_week_formation w
where w.formation_code is not null
  and not exists (select 1 from core.formation f where f.formation_code = w.formation_code)
union all
select null, 'no fallback formation (is_fallback)'
where not exists (select 1 from core.formation where is_fallback)
  and exists (select 1 from core.v_team_week_formation);

comment on view qa.formation_unknown is 'Formations missing from core.formation (names come from db/seed/001).';


-- Not a problem list: how much of each season's starting lineups the identity measures can see.
create or replace view qa.identity_coverage as
select season,
       count(*)                                                              as teams,
       round(avg(age_coverage) * 100, 1)                                     as pct_starts_with_birth_date,
       count(*) filter (where avg_age is null)                               as teams_without_age,
       round(avg(other_source_share) * 100, 1)                               as pct_points_unknown_or_other_source,
       round(avg((air_points + ground_points) / nullif(starter_points, 0)) * 100, 1)
                                                                             as pct_points_split_air_ground
from core.v_team_profile
group by season
order by season;

comment on view qa.identity_coverage is 'Info: birth-date coverage, unknown roster sources and stat-line coverage per season.';


-- Not a problem list: our best-possible-lineup totals vs Sleeper's own "potential points"
-- (rosters settings ppts), regular season, completed Sleeper seasons. A close match means
-- the bench numbers on the Lineups page agree with Sleeper. Sleeper may treat injured-reserve
-- players differently, so small gaps are possible.
create or replace view qa.best_lineup_vs_sleeper as
with sleeper as (
    select r.season, (e ->> 'roster_id')::int as roster_id,
           round((e -> 'settings' ->> 'ppts')::numeric
                 + coalesce((e -> 'settings' ->> 'ppts_decimal')::numeric, 0) / 100, 2) as sleeper_potential_points
    from raw.v_sleeper_latest r, jsonb_array_elements(r.payload) e
    where r.endpoint = 'rosters'
),
ours as (
    select season, franchise_code, sum(best_points) as best_points
    from core.v_lineup_week
    where is_regular_season
    group by season, franchise_code
)
select o.season, o.franchise_code, round(o.best_points, 2) as our_best_points,
       sl.sleeper_potential_points, round(o.best_points - sl.sleeper_potential_points, 2) as diff
from ours o
join core.season s on s.season = o.season and s.status = 'complete'
join core.franchise_season fs on fs.season = o.season and fs.franchise_code = o.franchise_code
join sleeper sl on sl.season = o.season and sl.roster_id = fs.sleeper_roster_id
order by abs(o.best_points - sl.sleeper_potential_points) desc;

comment on view qa.best_lineup_vs_sleeper is
    'Info: season best-lineup points (ours) vs Sleeper''s potential points (ppts), per team, regular season.';


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
union all select 'nfl_id_suspect',                      count(*) from qa.nfl_id_suspect
union all select 'league_defenses_without_stat_line',   count(*) from qa.league_defenses_without_stat_line
union all select 'unknown_lineup_slots',                count(*) from qa.unknown_lineup_slots
union all select 'best_lineup_below_actual',            count(*) from qa.best_lineup_below_actual
union all select 'player_log_duplicates',               count(*) from qa.player_log_duplicates
union all select 'def_points_accuracy_low',             count(*) from qa.def_points_accuracy_low
union all select 'trade_vs_acquisitions',               count(*) from qa.trade_vs_acquisitions
union all select 'traded_pick_unmatched',               count(*) from qa.traded_pick_unmatched
union all select 'replacement_level_mismatch',          count(*) from qa.replacement_level_mismatch
union all select 'points_split_mismatch',               count(*) from qa.points_split_mismatch
union all select 'identity_metric_unknown',             count(*) from qa.identity_metric_unknown
union all select 'formation_unknown',                   count(*) from qa.formation_unknown;

comment on view qa.summary is 'Problem count for every check. All zeros means the history is consistent.';
