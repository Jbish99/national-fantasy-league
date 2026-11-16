-- =====================================================================
-- db/schema/014_nfl_extras.sql
-- Phase 7b: more NFL data from nflverse, loaded by etl/load_nflverse.py
-- (steps snaps, injuries, opportunity, pbp):
--   core.nfl_snap_week          offensive / special-teams snaps per player-week
--   core.nfl_injury_week        the game-status injury report per player-week (Out / Doubtful / Questionable)
--   core.nfl_opportunity_week   nflverse's expected stats from usage (ff_opportunity), next to the actual ones
--   core.nfl_usage_week         red-zone, goal-line, end-zone and deep usage per player-week (from play-by-play)
--   core.nfl_team_offense_week  each NFL team's plays and pass rate per week (from play-by-play)
--   core.nfl_def_week           defense events Sleeper scores that need play-by-play: 3-and-outs, 4th-down
--                               stops, special-teams fumble recoveries, points allowed by the defense
-- and views that score them with our league's rules:
--   core.v_expected_league_points   expected points from usage, our scoring
--   core.v_def_league_points        team defense (DEF) fantasy points rebuilt with our scoring
--   core.v_def_week_conditions      DEF points by weather (joins the 013 weather splits)
--   core.v_position_conditions      013's weather-by-position, now with DEF
--   core.v_player_week_extras       snaps, injury status, expected points and usage per rostered player-week
--   core.v_started_while_out        starters who were listed Out or Doubtful that week
--
-- Raw files (incl. play-by-play, ~13 MB a season) stay in data/cache/nflverse/ on your computer;
-- only these compact tables go in the database. Regular season only. Seasons: whatever core.season has.
-- Run after 013. Safe to re-run.
-- =====================================================================

create table if not exists core.nfl_snap_week (
    gsis_id        text     not null,
    season         smallint not null,
    week           smallint not null,
    game_id        text,
    team           text,
    position       text,
    pfr_player_id  text,                          -- Pro Football Reference id (the snap counts' own key)
    offense_snaps  smallint,
    offense_pct    numeric(4,3),                  -- 0.000-1.000 share of the team's offensive snaps
    st_snaps       smallint,
    st_pct         numeric(4,3),
    link_method    text     not null check (link_method in ('pfr_id', 'name')),  -- how the row found its gsis_id
    primary key (gsis_id, season, week)
);
comment on table core.nfl_snap_week is
    'Snap counts per player-week (QB/RB/WR/TE/K/FB, regular season), linked to gsis ids through nflverse''s player '
    'list (pfr_id), or by name + team + position when that list has no link.';


create table if not exists core.nfl_injury_week (
    gsis_id                  text     not null,
    season                   smallint not null,
    week                     smallint not null,
    team                     text,
    position                 text,
    report_status            text,           -- game status: Out, Doubtful, Questionable (null = practice report only)
    report_primary_injury    text,
    report_secondary_injury  text,
    practice_status          text,
    primary key (gsis_id, season, week)
);
comment on table core.nfl_injury_week is
    'The official NFL injury report per player-week (fantasy positions, regular season). Players on injured reserve '
    'are not on the weekly report.';


create table if not exists core.nfl_opportunity_week (
    gsis_id                    text     not null,
    season                     smallint not null,
    week                       smallint not null,
    game_id                    text,
    team                       text,
    position                   text,
    pass_attempt               real            ,
    rec_attempt                real            ,   -- targets
    rush_attempt               real            ,
    pass_completions           real            ,
    pass_completions_exp       real            ,
    receptions                 real            ,
    receptions_exp             real            ,
    pass_yards_gained          real            ,
    pass_yards_gained_exp      real            ,
    rec_yards_gained           real            ,
    rec_yards_gained_exp       real            ,
    rush_yards_gained          real            ,
    rush_yards_gained_exp      real            ,
    pass_touchdown             real            ,
    pass_touchdown_exp         real            ,
    rec_touchdown              real            ,
    rec_touchdown_exp          real            ,
    rush_touchdown             real            ,
    rush_touchdown_exp         real            ,
    pass_two_point_conv        real            ,
    pass_two_point_conv_exp    real            ,
    rec_two_point_conv         real            ,
    rec_two_point_conv_exp     real            ,
    rush_two_point_conv        real            ,
    rush_two_point_conv_exp    real            ,
    pass_first_down            real            ,   -- first downs INCLUDE touchdown plays here (like nflverse's stats)
    pass_first_down_exp        real            ,
    rec_first_down             real            ,
    rec_first_down_exp         real            ,
    rush_first_down            real            ,
    rush_first_down_exp        real            ,
    pass_interception          real            ,
    pass_interception_exp      real            ,
    rec_fumble_lost            real            ,
    rush_fumble_lost           real            ,
    primary key (gsis_id, season, week)
);
comment on table core.nfl_opportunity_week is
    'nflverse ff_opportunity: for every player-week, the stats a player was expected to produce from his usage '
    '(where and how often he got the ball), next to what he produced. Re-scored with league rules in core.v_expected_league_points. '
    'Stored as real (4-byte) numbers to keep the table small; plenty for points shown to 2 decimals.';


create table if not exists core.nfl_usage_week (
    gsis_id        text     not null,
    season         smallint not null,
    week           smallint not null,
    team           text,
    targets_rz     smallint not null,   -- targets inside the opponent's 20
    targets_ez     smallint not null,   -- targets thrown into the end zone (air yards >= yards to the goal line)
    targets_deep   smallint not null,   -- targets 20+ air yards downfield
    carries_rz     smallint not null,   -- carries inside the opponent's 20
    carries_gl     smallint not null,   -- carries inside the opponent's 5
    primary key (gsis_id, season, week)
);
comment on table core.nfl_usage_week is
    'Red-zone, goal-line, end-zone and deep usage per player-week, counted from play-by-play (no penalties-nullified '
    'plays, no 2-point tries, no kneels).';


create table if not exists core.nfl_team_offense_week (
    team               text     not null,
    season             smallint not null,
    week               smallint not null,
    game_id            text,
    plays              smallint not null,   -- dropbacks + designed runs
    dropbacks          smallint not null,   -- pass attempts, sacks and scrambles
    rushes             smallint not null,   -- designed runs (no kneels)
    neutral_plays      smallint not null,   -- 1st/2nd down, win probability 20-80%, not the last 2 minutes of a half
    neutral_dropbacks  smallint not null,
    rz_plays           smallint not null,   -- plays inside the opponent's 20
    primary key (team, season, week)
);
comment on table core.nfl_team_offense_week is
    'How each NFL offense called its plays each week (pass rate overall and in neutral situations), from play-by-play.';


create table if not exists core.nfl_def_week (
    team                   text     not null,   -- nflverse team code (LA = Rams)
    season                 smallint not null,
    week                   smallint not null,
    game_id                text,
    opponent_team          text,
    three_and_outs         smallint not null,   -- opponent drives ending in a punt without a first down
    fourth_down_stops      smallint not null,   -- opponent 4th-down tries that failed
    st_fumble_recoveries   smallint not null,   -- fumbles recovered on kickoff / punt plays
    opponent_points        smallint,            -- what the opponent scored in the game
    points_allowed         smallint,            -- ...minus points scored while this defense wasn't on the field
                                                --    (return TDs incl. their conversion, safeties, defensive 2-pt returns)
    primary key (team, season, week)
);
comment on table core.nfl_def_week is
    'Defense events Sleeper scores that need play-by-play. Checked against Sleeper''s real 2025 DEF points: 28 of 29 '
    'team-weeks exact (the miss: half a point, a passes-defended count that differs between stat providers).';


-- ---------------------------------------------------------------------
-- Expected points with our league's rules
-- ---------------------------------------------------------------------
-- The same scoring parts on both sides (yards, TDs, catches, first downs, 2-pt, INTs; TE bonus by position),
-- so points_over_expected is fair. Fumbles, return TDs and kicking aren't in nflverse's model, so they're left
-- out of both sides; these numbers are a little different from a player's real Sleeper points.
-- First downs: Sleeper doesn't pay the first-down bonus on a TD play, so expected TDs come out of expected
-- first downs (as in core.v_nfl_league_points).
create or replace view core.v_expected_league_points as
select
    o.gsis_id, o.season, o.week, o.team, o.position,
    o.pass_attempt, o.rec_attempt as targets, o.rush_attempt as carries,
    round((
          coalesce(o.pass_yards_gained_exp, 0) * s.pass_yd + coalesce(o.pass_touchdown_exp, 0) * s.pass_td
        + coalesce(o.pass_interception_exp, 0) * s.pass_int + coalesce(o.pass_two_point_conv_exp, 0) * s.pass_2pt
        + greatest(coalesce(o.pass_first_down_exp, 0) - coalesce(o.pass_touchdown_exp, 0), 0) * s.pass_fd
        + coalesce(o.pass_attempt, 0) * s.pass_att + coalesce(o.pass_completions_exp, 0) * s.pass_cmp
        + (coalesce(o.pass_attempt, 0) - coalesce(o.pass_completions_exp, 0)) * s.pass_inc
        + coalesce(o.rush_yards_gained_exp, 0) * s.rush_yd + coalesce(o.rush_touchdown_exp, 0) * s.rush_td
        + coalesce(o.rush_two_point_conv_exp, 0) * s.rush_2pt + coalesce(o.rush_attempt, 0) * s.rush_att
        + greatest(coalesce(o.rush_first_down_exp, 0) - coalesce(o.rush_touchdown_exp, 0), 0) * s.rush_fd
        + coalesce(o.receptions_exp, 0) * (s.rec + case o.position when 'TE' then s.bonus_rec_te
                                                                   when 'RB' then s.bonus_rec_rb
                                                                   when 'WR' then s.bonus_rec_wr else 0 end)
        + coalesce(o.rec_yards_gained_exp, 0) * s.rec_yd + coalesce(o.rec_touchdown_exp, 0) * s.rec_td
        + coalesce(o.rec_two_point_conv_exp, 0) * s.rec_2pt
        + greatest(coalesce(o.rec_first_down_exp, 0) - coalesce(o.rec_touchdown_exp, 0), 0) * s.rec_fd
    )::numeric, 2) as expected_points,
    round((
          coalesce(o.pass_yards_gained, 0) * s.pass_yd + coalesce(o.pass_touchdown, 0) * s.pass_td
        + coalesce(o.pass_interception, 0) * s.pass_int + coalesce(o.pass_two_point_conv, 0) * s.pass_2pt
        + greatest(coalesce(o.pass_first_down, 0) - coalesce(o.pass_touchdown, 0), 0) * s.pass_fd
        + coalesce(o.pass_attempt, 0) * s.pass_att + coalesce(o.pass_completions, 0) * s.pass_cmp
        + (coalesce(o.pass_attempt, 0) - coalesce(o.pass_completions, 0)) * s.pass_inc
        + coalesce(o.rush_yards_gained, 0) * s.rush_yd + coalesce(o.rush_touchdown, 0) * s.rush_td
        + coalesce(o.rush_two_point_conv, 0) * s.rush_2pt + coalesce(o.rush_attempt, 0) * s.rush_att
        + greatest(coalesce(o.rush_first_down, 0) - coalesce(o.rush_touchdown, 0), 0) * s.rush_fd
        + coalesce(o.receptions, 0) * (s.rec + case o.position when 'TE' then s.bonus_rec_te
                                                               when 'RB' then s.bonus_rec_rb
                                                               when 'WR' then s.bonus_rec_wr else 0 end)
        + coalesce(o.rec_yards_gained, 0) * s.rec_yd + coalesce(o.rec_touchdown, 0) * s.rec_td
        + coalesce(o.rec_two_point_conv, 0) * s.rec_2pt
        + greatest(coalesce(o.rec_first_down, 0) - coalesce(o.rec_touchdown, 0), 0) * s.rec_fd
    )::numeric, 2) as actual_points
from core.nfl_opportunity_week o
join core.v_scoring_rules s on s.season = o.season;

comment on view core.v_expected_league_points is
    'Expected vs actual points per player-week from nflverse''s usage model, both scored with league rules on the '
    'same parts (no fumbles, return TDs or kicking). actual_points - expected_points = points over expected.';


-- ---------------------------------------------------------------------
-- Team defense (DEF) points rebuilt with our league's rules
-- ---------------------------------------------------------------------
-- Every DEF rule Sleeper has is looked up in core.scoring_setting for that season, so a rule that's off
-- (or a bucket the league never set, like yds_allow_500_549 in 2021-22) scores 0, exactly like Sleeper.
-- Points allowed = points the defense was on the field for (see core.nfl_def_week.points_allowed).
-- Yards allowed = opponent passing yards (net of sacks) + rushing yards (core.nfl_team_week.net_yards_allowed).
-- Fumble recoveries on kickoffs/punts score as def_st_fum_rec, the rest as fum_rec.
create or replace view core.v_def_league_points as
with base as (
    select
        t.team, t.sleeper_def_id, t.season, t.week, t.game_id, t.opponent_team,
        coalesce(t.def_sacks, 0)                                                          as sacks,
        coalesce(t.def_interceptions, 0)                                                  as interceptions,
        greatest(coalesce(t.fumble_recovery_opp, 0) - d.st_fumble_recoveries, 0)         as fumble_recoveries,
        d.st_fumble_recoveries,
        coalesce(t.def_fumbles_forced, 0)                                                 as forced_fumbles,
        coalesce(t.def_tds, 0)                                                            as defensive_tds,
        coalesce(t.special_teams_tds, 0)                                                  as return_tds,
        coalesce(t.def_safeties, 0)                                                       as safeties,
        coalesce(t.def_punt_blocks, 0) + coalesce(t.def_fg_blocks, 0) + coalesce(t.def_pat_blocks, 0) as blocked_kicks,
        coalesce(t.def_2pt_made, 0)                                                       as def_2pt_returns,
        coalesce(t.def_tackles_for_loss, 0)                                               as tackles_for_loss,
        coalesce(t.def_pass_defended, 0)                                                  as passes_defended,
        d.three_and_outs,
        d.fourth_down_stops,
        d.points_allowed,
        t.net_yards_allowed                                                               as yards_allowed
    from core.nfl_team_week t
    join core.nfl_def_week d on d.team = t.team and d.season = t.season and d.week = t.week
    where t.season_type = 'REG'
      and t.season in (select season from core.scoring_setting)   -- Sleeper seasons (2020 was ESPN)
),
keyed as (
    select b.team, b.season, b.week, k.stat_key, k.amount
    from base b
    cross join lateral (values
        ('sack', b.sacks), ('int', b.interceptions::numeric), ('fum_rec', b.fumble_recoveries::numeric),
        ('def_st_fum_rec', b.st_fumble_recoveries::numeric), ('ff', b.forced_fumbles::numeric),
        ('def_td', b.defensive_tds::numeric), ('def_st_td', b.return_tds::numeric), ('safe', b.safeties::numeric),
        ('blk_kick', b.blocked_kicks::numeric), ('def_2pt', b.def_2pt_returns::numeric),
        ('tkl_loss', b.tackles_for_loss::numeric), ('def_pass_def', b.passes_defended::numeric),
        ('def_3_and_out', b.three_and_outs::numeric), ('def_4_and_stop', b.fourth_down_stops::numeric),
        ('pts_allow', b.points_allowed::numeric), ('yds_allow', b.yards_allowed::numeric),
        (case when b.points_allowed is null then null
              when b.points_allowed = 0 then 'pts_allow_0'      when b.points_allowed <= 6 then 'pts_allow_1_6'
              when b.points_allowed <= 13 then 'pts_allow_7_13' when b.points_allowed <= 20 then 'pts_allow_14_20'
              when b.points_allowed <= 27 then 'pts_allow_21_27' when b.points_allowed <= 34 then 'pts_allow_28_34'
              else 'pts_allow_35p' end, 1::numeric),
        (case when b.yards_allowed is null then null
              when b.yards_allowed < 100 then 'yds_allow_0_100'   when b.yards_allowed < 200 then 'yds_allow_100_199'
              when b.yards_allowed < 300 then 'yds_allow_200_299' when b.yards_allowed < 350 then 'yds_allow_300_349'
              when b.yards_allowed < 400 then 'yds_allow_350_399' when b.yards_allowed < 450 then 'yds_allow_400_449'
              when b.yards_allowed < 500 then 'yds_allow_450_499' when b.yards_allowed < 550 then 'yds_allow_500_549'
              else 'yds_allow_550p' end, 1::numeric)
    ) as k(stat_key, amount)
    where k.stat_key is not null
),
scored as (
    select k.team, k.season, k.week, round(sum(k.amount * coalesce(s.points, 0)), 2) as league_points
    from keyed k
    left join core.scoring_setting s on s.season = k.season and s.stat_key = k.stat_key
    group by k.team, k.season, k.week
)
select b.*, sc.league_points
from base b
join scored sc on sc.team = b.team and sc.season = b.season and sc.week = b.week;

comment on view core.v_def_league_points is
    'Team defense fantasy points per NFL team-week, rebuilt from nflverse with each season''s league DEF rules. '
    'qa.def_points_accuracy compares them with Sleeper''s.';


-- DEF weather splits (same buckets and "vs season average" measure as core.v_player_week_conditions)
create or replace view core.v_def_week_conditions as
select
    d.sleeper_def_id, d.team, d.season, d.week, d.game_id as nfl_game_id, d.league_points,
    count(*) over w                                            as games_that_season,
    round(avg(d.league_points) over w, 2)                      as season_avg,
    round(d.league_points - avg(d.league_points) over w, 2)    as vs_season_avg,
    c.temp, c.wind, c.is_dome, c.is_cold, c.is_windy, c.is_mild, c.weather_not_on_file
from core.v_def_league_points d
join core.v_nfl_game_conditions c on c.game_id = d.game_id
where d.season >= 2021
window w as (partition by d.team, d.season);

comment on view core.v_def_week_conditions is
    'Every NFL team defense''s regular-season weeks (2021 ->) with rebuilt DEF points vs its season average, and the conditions.';


-- 013's weather-by-position, now with DEF (defenses started in the league that season). Same columns.
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
league_defenses as (
    select distinct pw.season, pw.player_id as sleeper_def_id
    from core.player_week pw
    join core.player p on p.player_id = pw.player_id
    where pw.is_starter and p.position = 'DEF'
),
weeks as (
    select lp.position, w.season, w.league_points, w.vs_season_avg,
           w.is_dome, w.is_mild, w.is_cold, w.is_windy, w.weather_not_on_file
    from core.v_player_week_conditions w
    join league_players lp on lp.season = w.season and lp.gsis_id = w.gsis_id
    union all
    select 'DEF', d.season, d.league_points, d.vs_season_avg,
           d.is_dome, d.is_mild, d.is_cold, d.is_windy, d.weather_not_on_file
    from core.v_def_week_conditions d
    join league_defenses ld on ld.season = d.season and ld.sleeper_def_id = d.sleeper_def_id
),
by_condition as (
    select w.position, w.season, b.condition, b.sort_order, w.league_points, w.vs_season_avg
    from weeks w
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
    'Points per game and points vs the player''s season average, by position and weather, for players (and defenses) '
    'started in the league that season.';


-- ---------------------------------------------------------------------
-- Per rostered player-week: snaps, injury report, expected points, usage
-- ---------------------------------------------------------------------
create or replace view core.v_player_week_extras as
select
    pw.player_id, pw.season, pw.week,
    s.offense_snaps, s.offense_pct, s.st_snaps,
    i.report_status            as injury_status,
    i.report_primary_injury    as injury,
    i.practice_status,
    e.expected_points,
    e.actual_points            as actual_points_same_parts,
    e.actual_points - e.expected_points as points_over_expected,
    e.targets, e.carries,
    u.targets_rz, u.targets_ez, u.targets_deep, u.carries_rz, u.carries_gl
from core.player_week pw
left join core.player_xref x            on x.player_id = pw.player_id
left join core.nfl_snap_week s          on s.gsis_id = x.gsis_id and s.season = pw.season and s.week = pw.week
left join core.nfl_injury_week i        on i.gsis_id = x.gsis_id and i.season = pw.season and i.week = pw.week
left join core.v_expected_league_points e on e.gsis_id = x.gsis_id and e.season = pw.season and e.week = pw.week
left join core.nfl_usage_week u         on u.gsis_id = x.gsis_id and u.season = pw.season and u.week = pw.week;

comment on view core.v_player_week_extras is
    'For each rostered player-week: snap share, injury report status, expected points (league rules) and red-zone usage.';


create or replace view core.v_started_while_out as
select
    pw.season, pw.week, (pw.week < se.playoff_week_start) as is_regular_season,
    pw.franchise_code, pw.player_id,
    coalesce(p.full_name, trim(coalesce(p.first_name, '') || ' ' || coalesce(p.last_name, ''))) as player_name,
    p.position, pw.lineup_slot, pw.points,
    x.injury_status, x.injury, x.offense_snaps
from core.player_week pw
join core.season se             on se.season = pw.season
join core.player p              on p.player_id = pw.player_id
join core.v_player_week_extras x on x.player_id = pw.player_id and x.season = pw.season and x.week = pw.week
where pw.is_starter
  and x.injury_status in ('Out', 'Doubtful');

comment on view core.v_started_while_out is
    'Starters who were listed Out or Doubtful on that week''s NFL injury report (Doubtful players sometimes play).';
