-- =====================================================================
-- db/schema/003_views.sql
-- Core views the app and the validation checks build on.
-- Safe to re-run (CREATE OR REPLACE).
-- =====================================================================

-- ---------------------------------------------------------------------
-- core.v_team_week_result
-- One row per franchise per week with opponent, score and result.
-- Official numbers = Sleeper's, unless core.result_override says otherwise.
-- ---------------------------------------------------------------------
create or replace view core.v_team_week_result as
with scored as (
    select
        tw.season,
        tw.week,
        tw.franchise_code,
        tw.matchup_id,
        coalesce(ro.official_points, tw.points) as points,
        ro.official_result
    from core.team_week tw
    left join core.result_override ro
           on ro.season = tw.season
          and ro.week = tw.week
          and ro.franchise_code = tw.franchise_code
)
select
    me.season,
    me.week,
    case when me.week < s.playoff_week_start then 'regular' else 'postseason' end as phase,
    me.franchise_code,
    fs.team_name,
    fs.owner_id,
    me.matchup_id,
    me.points                    as points_for,
    opp.franchise_code           as opponent_code,
    opp.points                   as points_against,
    coalesce(
        me.official_result,
        -- an override on the opponent decides this side too
        case opp.official_result when 'W' then 'L' when 'L' then 'W' when 'T' then 'T' end,
        case
            when opp.franchise_code is null  then null
            when me.points > opp.points      then 'W'
            when me.points < opp.points      then 'L'
            else 'T'
        end
    )                            as result,
    me.points - opp.points       as margin
from scored me
join core.season s
  on s.season = me.season
join core.franchise_season fs
  on fs.season = me.season
 and fs.franchise_code = me.franchise_code
left join scored opp
       on opp.season = me.season
      and opp.week = me.week
      and opp.matchup_id = me.matchup_id
      and opp.franchise_code <> me.franchise_code;

comment on view core.v_team_week_result is
    'Each franchise''s weekly score, opponent, result and margin. Applies core.result_override.';


-- ---------------------------------------------------------------------
-- core.v_regular_season_record
-- Regular-season W/L/T and points per franchise per season.
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
from core.v_team_week_result
where phase = 'regular'
  and opponent_code is not null
group by season, franchise_code;

comment on view core.v_regular_season_record is 'Regular-season record and points for each franchise-season.';


-- ---------------------------------------------------------------------
-- core.v_champion
-- Champion and runner-up per season (winners bracket, place 1 game).
-- ---------------------------------------------------------------------
create or replace view core.v_champion as
select
    pg.season,
    pg.winner_code  as champion_code,
    w.team_name     as champion_team_name,
    pg.loser_code   as runner_up_code,
    l.team_name     as runner_up_team_name
from core.playoff_game pg
join core.franchise_season w on w.season = pg.season and w.franchise_code = pg.winner_code
join core.franchise_season l on l.season = pg.season and l.franchise_code = pg.loser_code
where pg.bracket = 'winners'
  and pg.place_decided = 1;

comment on view core.v_champion is 'Fantasy Bowl champion and runner-up by season.';
