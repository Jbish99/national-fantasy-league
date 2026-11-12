-- =====================================================================
-- db/schema/003_views.sql
-- Views the app and the validation checks build on.
--   v_team_game, v_regular_season_record, v_champion -> OFFICIAL results (core.game)
--   v_sleeper_team_week, v_sleeper_season_record     -> Sleeper's numbers (for checks and lineup analysis)
-- Safe to re-run (CREATE OR REPLACE).
-- =====================================================================

-- ---------------------------------------------------------------------
-- core.v_team_game  (OFFICIAL)
-- Every official game from each team's point of view: two rows per game.
-- ---------------------------------------------------------------------
create or replace view core.v_team_game as
with sides as (
    select g.*, g.team1_code as me, g.team2_code as opp, g.team1_points as pf, g.team2_points as pa from core.game g
    union all
    select g.*, g.team2_code,       g.team1_code,        g.team2_points,        g.team1_points       from core.game g
)
select
    s.game_id,
    s.season,
    s.week,
    s.game_type,                 -- regular, playoff, consolation
    s.designation,               -- Fantasy Bowl, 3rd Place Bowl, IST Game...
    s.me                         as franchise_code,
    fs.team_name,
    fs.owner_id,
    s.opp                        as opponent_code,
    s.pf                         as points_for,
    s.pa                         as points_against,
    case
        when s.winner_code = s.me  then 'W'
        when s.winner_code = s.opp then 'L'
        else 'T'
    end                          as result,
    s.pf - s.pa                  as margin,
    s.points_source,
    s.result_note,
    s.international_location,
    s.is_game_of_week,
    s.is_division_game,
    s.is_conference_game
from sides s
join core.franchise_season fs
  on fs.season = s.season
 and fs.franchise_code = s.me;

comment on view core.v_team_game is
    'OFFICIAL results: each game from each team''s side (two rows per game). The app''s main source for results.';


-- ---------------------------------------------------------------------
-- core.v_regular_season_record  (OFFICIAL)
-- ---------------------------------------------------------------------
create or replace view core.v_regular_season_record as
select
    season,
    franchise_code,
    count(*) filter (where result = 'W') as wins,
    count(*) filter (where result = 'L') as losses,
    count(*) filter (where result = 'T') as ties,
    sum(points_for)                      as points_for,
    sum(points_against)                  as points_against
from core.v_team_game
where game_type = 'regular'
group by season, franchise_code;

comment on view core.v_regular_season_record is 'OFFICIAL regular-season record and points for each franchise-season.';


-- ---------------------------------------------------------------------
-- core.v_champion  (OFFICIAL)
-- One row per champion. Co-champion seasons (2022) get two rows.
-- ---------------------------------------------------------------------
create or replace view core.v_champion as
select
    g.season,
    t.franchise_code   as champion_code,
    t.team_name        as champion_team_name,
    case when t.result = 'W' then t.opponent_code end as runner_up_code,
    (g.winner_code is null)                           as is_co_champion,
    g.result_note
from core.game g
join core.v_team_game t
  on t.game_id = g.game_id
where g.designation = 'Fantasy Bowl'
  and (t.result = 'W' or g.winner_code is null);

comment on view core.v_champion is 'Fantasy Bowl champion(s) by season. Co-champions appear as two rows with is_co_champion = true.';


-- ---------------------------------------------------------------------
-- core.v_sleeper_team_week  (SLEEPER)
-- Sleeper's weekly scores paired by matchup_id. No official overrides.
-- ---------------------------------------------------------------------
create or replace view core.v_sleeper_team_week as
select
    me.season,
    me.week,
    case when me.week < s.playoff_week_start then 'regular' else 'postseason' end as phase,
    me.franchise_code,
    me.matchup_id,
    me.points                    as points_for,
    opp.franchise_code           as opponent_code,
    opp.points                   as points_against,
    case
        when opp.franchise_code is null then null
        when me.points > opp.points     then 'W'
        when me.points < opp.points     then 'L'
        else 'T'
    end                          as result
from core.team_week me
join core.season s
  on s.season = me.season
left join core.team_week opp
       on opp.season = me.season
      and opp.week = me.week
      and opp.matchup_id = me.matchup_id
      and opp.franchise_code <> me.franchise_code;

comment on view core.v_sleeper_team_week is 'Sleeper''s weekly scores with opponent and result, exactly as Sleeper has them.';


-- ---------------------------------------------------------------------
-- core.v_sleeper_season_record  (SLEEPER)
-- ---------------------------------------------------------------------
create or replace view core.v_sleeper_season_record as
select
    season,
    franchise_code,
    count(*) filter (where result = 'W') as wins,
    count(*) filter (where result = 'L') as losses,
    count(*) filter (where result = 'T') as ties,
    sum(points_for)                      as points_for,
    sum(points_against)                  as points_against
from core.v_sleeper_team_week
where phase = 'regular'
  and opponent_code is not null
group by season, franchise_code;

comment on view core.v_sleeper_season_record is 'Regular-season totals computed from Sleeper''s weekly scores. Used to validate the Sleeper load.';
