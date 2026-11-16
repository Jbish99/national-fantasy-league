-- =====================================================================
-- db/schema/016_team_identities.sql
-- Phase 10: team identities ("Air-Raid", "Ground and Pound", ...).
--
-- Decisions (Jaden, 2026-11-16):
--   * Every team-season (2021 on, regular season, starters only) gets ONE style
--     label (how it scored) and ONE build label (how its roster was put together).
--   * A label is earned when the team "clearly stands out": at least 1 standard
--     deviation from that season's league average in the label's direction
--     (core.identity.min_z, 1.0 for every label today). Trade Shark and Waiver Wire
--     Warrior also need 10% of the points from that source (min_value; Claude's call
--     2026-11-16, after 2% earned Trade Shark in a season when nobody traded). If no label of a kind
--     qualifies, the team gets that kind's fallback ("Well-Rounded" / "Mixed Build").
--     When several qualify, the one where the team stands out most wins.
--   * Both season-by-season and all-time. All-time = the label a franchise leaned
--     toward most on average over its seasons (its average score), if that average is
--     at least core.identity.min_alltime_z (0.5 standard deviations: averages over
--     several seasons shrink toward 0, so 1.0 would almost never be reached); 2+ seasons.
--     (Claude's call, 2026-11-16: standardizing the franchises' averages against each
--     other was tried first and turned tiny leans into labels.)
--   * The 14 labels, their names, one-line descriptions and thresholds live in
--     db/seed/001_reference_data.sql (table core.identity): rename there.
--
-- The measures (core.v_team_profile), regular season, the team's starters:
--   ground_share   rushing points / (rushing + passing + receiving points), rebuilt from
--                  real stat lines with that season's scoring (core.v_nfl_points_split).
--                  QB scrambles count as ground; RB catches count as through the air.
--   qb_share       share of starters' points scored by the QB (Sleeper points)
--   top2_share     share of starters' points from the team's 2 best starters that season
--   score_cv       week-to-week swing: standard deviation / average of official weekly scores
--   drafted_share / kept_share / trade_share / pickup_share
--                  share of starters' points by how the player got onto that roster
--                  (core.v_player_week_source; pickup = waiver or free agent)
--   avg_age        average age (on Sept 1) of a starter, per start (birth dates from Sleeper)
--   bench_ppw      points scored on the bench per week
--
-- New here: core.identity (table), core.v_nfl_points_split, core.v_player_week_source,
-- core.v_team_profile, core.v_team_metric, core.v_team_identity_score,
-- core.v_team_identity, core.v_franchise_identity.
--
-- Run after 015. Safe to re-run.
-- =====================================================================


-- ---------------------------------------------------------------------
-- The labels. Rows are in db/seed/001_reference_data.sql (edit names there).
-- metric = a measure in core.v_team_metric; direction 1 = a high value earns it,
-- -1 = a low value. A row with no metric is its kind's fallback. min_value (optional,
-- for direction 1) = the value must also reach this (e.g. Trade Shark: 10% of the points
-- from trades, so 2% in a season when nobody traded doesn't earn it).
-- evidence = the sentence the app shows, with {value} and {avg} filled in
-- using value_format (pct, pct_inverse = 1 - value, years, points).
-- ---------------------------------------------------------------------
create table if not exists core.identity (
    identity_code  text     primary key,
    name           text     not null,
    kind           text     not null check (kind in ('style', 'build')),
    metric         text,
    direction      smallint check (direction in (1, -1)),
    min_z          numeric  not null default 1.0,
    min_alltime_z  numeric  not null default 0.5,
    min_value      numeric,
    description    text     not null,
    evidence       text,
    value_format   text     check (value_format in ('pct', 'pct_inverse', 'years', 'points')),
    sort_order     smallint not null default 0,
    check ((metric is null) = (direction is null))
);
create unique index if not exists ux_identity_fallback on core.identity (kind) where metric is null;

comment on table core.identity is
    'Team identity labels (Phase 10): name, style or build, which measure earns it, threshold. Rows in db/seed/001.';


-- ---------------------------------------------------------------------
-- Every NFL stat line's league points, split by how they were scored. The parts
-- add up to core.v_nfl_league_points.league_points (qa.points_split_mismatch).
-- Fumbles go with the play they came from (sack -> passing, run -> rushing,
-- catch -> receiving). other = combined rush+rec yardage bonuses, returns,
-- fumble-recovery TDs.
-- ---------------------------------------------------------------------
create or replace view core.v_nfl_points_split as
select
    n.gsis_id, n.season, n.week, n.season_type, n.position,
    (     coalesce(n.passing_yards, 0) * s.pass_yd
        + coalesce(n.passing_tds, 0) * s.pass_td
        + coalesce(n.passing_interceptions, 0) * s.pass_int
        + coalesce(n.passing_2pt_conversions, 0) * s.pass_2pt
        + (coalesce(n.passing_first_downs, 0) - least(coalesce(n.passing_tds, 0), coalesce(n.passing_first_downs, 0))) * s.pass_fd
        + coalesce(n.attempts, 0) * s.pass_att
        + coalesce(n.completions, 0) * s.pass_cmp
        + (coalesce(n.attempts, 0) - coalesce(n.completions, 0)) * s.pass_inc
        + coalesce(n.sacks_suffered, 0) * s.pass_sack
        + (case when coalesce(n.passing_yards, 0) >= 300 then 1 else 0 end) * s.bonus_pass_yd_300
        + (case when coalesce(n.passing_yards, 0) >= 400 then 1 else 0 end) * s.bonus_pass_yd_400
        + (case when coalesce(n.completions, 0) >= 25 then 1 else 0 end) * s.bonus_pass_cmp_25
        + coalesce(n.sack_fumbles, 0) * s.fum
        + coalesce(n.sack_fumbles_lost, 0) * s.fum_lost
    )                                                                                   as passing_points,
    (     coalesce(n.rushing_yards, 0) * s.rush_yd
        + coalesce(n.rushing_tds, 0) * s.rush_td
        + coalesce(n.rushing_2pt_conversions, 0) * s.rush_2pt
        + (coalesce(n.rushing_first_downs, 0) - least(coalesce(n.rushing_tds, 0), coalesce(n.rushing_first_downs, 0))) * s.rush_fd
        + coalesce(n.carries, 0) * s.rush_att
        + (case when coalesce(n.rushing_yards, 0) >= 100 then 1 else 0 end) * s.bonus_rush_yd_100
        + (case when coalesce(n.rushing_yards, 0) >= 200 then 1 else 0 end) * s.bonus_rush_yd_200
        + (case when coalesce(n.carries, 0) >= 20 then 1 else 0 end) * s.bonus_rush_att_20
        + coalesce(n.rushing_fumbles, 0) * s.fum
        + coalesce(n.rushing_fumbles_lost, 0) * s.fum_lost
    )                                                                                   as rushing_points,
    (     coalesce(n.receptions, 0) * s.rec
        + coalesce(n.receiving_yards, 0) * s.rec_yd
        + coalesce(n.receiving_tds, 0) * s.rec_td
        + coalesce(n.receiving_2pt_conversions, 0) * s.rec_2pt
        + (coalesce(n.receiving_first_downs, 0) - least(coalesce(n.receiving_tds, 0), coalesce(n.receiving_first_downs, 0))) * s.rec_fd
        + (case when n.position = 'TE' then coalesce(n.receptions, 0) else 0 end) * s.bonus_rec_te
        + (case when n.position = 'RB' then coalesce(n.receptions, 0) else 0 end) * s.bonus_rec_rb
        + (case when n.position = 'WR' then coalesce(n.receptions, 0) else 0 end) * s.bonus_rec_wr
        + (case when coalesce(n.receiving_yards, 0) >= 100 then 1 else 0 end) * s.bonus_rec_yd_100
        + (case when coalesce(n.receiving_yards, 0) >= 200 then 1 else 0 end) * s.bonus_rec_yd_200
        + coalesce(n.receiving_fumbles, 0) * s.fum
        + coalesce(n.receiving_fumbles_lost, 0) * s.fum_lost
    )                                                                                   as receiving_points,
    (     coalesce(n.pat_made, 0) * s.xpm
        + coalesce(n.pat_missed, 0) * s.xpmiss
        + coalesce(n.fg_made, 0) * s.fgm
        + coalesce(n.fg_missed, 0) * s.fgmiss
        + coalesce(n.fg_made_distance, 0) * s.fgm_yds
        + coalesce(n.fg_made_0_19, 0) * s.fgm_0_19
        + coalesce(n.fg_made_20_29, 0) * s.fgm_20_29
        + coalesce(n.fg_made_30_39, 0) * s.fgm_30_39
        + coalesce(n.fg_made_40_49, 0) * s.fgm_40_49
        + coalesce(n.fg_made_50_59, 0) * s.fgm_50_59
        + coalesce(n.fg_made_60_, 0) * s.fgm_60p
        + (coalesce(n.fg_made_50_59, 0) + coalesce(n.fg_made_60_, 0)) * s.fgm_50p
        + coalesce(n.fg_missed_0_19, 0) * s.fgmiss_0_19
        + coalesce(n.fg_missed_20_29, 0) * s.fgmiss_20_29
        + coalesce(n.fg_missed_30_39, 0) * s.fgmiss_30_39
        + coalesce(n.fg_missed_40_49, 0) * s.fgmiss_40_49
        + coalesce(n.fg_missed_50_59, 0) * s.fgmiss_50_59
        + coalesce(n.fg_missed_60_, 0) * s.fgmiss_60p
        + (coalesce(n.fg_missed_50_59, 0) + coalesce(n.fg_missed_60_, 0)) * s.fgmiss_50p
    )                                                                                   as kicking_points,
    (     (case when (coalesce(n.rushing_yards, 0) + coalesce(n.receiving_yards, 0)) >= 100 then 1 else 0 end) * s.bonus_rush_rec_yd_100
        + (case when (coalesce(n.rushing_yards, 0) + coalesce(n.receiving_yards, 0)) >= 200 then 1 else 0 end) * s.bonus_rush_rec_yd_200
        + coalesce(n.special_teams_tds, 0) * s.st_td
        + coalesce(n.fumble_recovery_tds, 0) * s.fum_rec_td
        + coalesce(n.kickoff_return_yards, 0) * s.kr_yd
        + coalesce(n.punt_return_yards, 0) * s.pr_yd
    )                                                                                   as other_points
from core.nfl_player_week n
join core.v_scoring_rules s on s.season = n.season;

comment on view core.v_nfl_points_split is
    'Every NFL stat line''s league points split into passing, rushing, receiving, kicking and other.';


-- ---------------------------------------------------------------------
-- How each rostered player-week's player got onto that roster: the team's most
-- recent add of him that season up to that week (trade / waiver / free_agent /
-- commissioner), else kept (core.keeper or a keeper pick), else drafted by that
-- team that season, else unknown.
-- ---------------------------------------------------------------------
create or replace view core.v_player_week_source as
with adds as (
    select t.season, t.week, t.created_at, t.type, tp.franchise_code, tp.player_id
    from core.transaction t
    join core.transaction_player tp on tp.transaction_id = t.transaction_id
    where t.status = 'complete' and tp.action = 'add'
      and t.type in ('trade', 'waiver', 'free_agent', 'commissioner')
),
latest_add as (
    select distinct on (pw.season, pw.week, pw.franchise_code, pw.player_id)
           pw.season, pw.week, pw.franchise_code, pw.player_id, a.type
    from core.player_week pw
    join adds a on a.season = pw.season and a.franchise_code = pw.franchise_code
               and a.player_id = pw.player_id and a.week <= pw.week
    order by pw.season, pw.week, pw.franchise_code, pw.player_id, a.week desc, a.created_at desc
),
kept as (
    select season, franchise_code, player_id from core.keeper
    union
    select season, franchise_code, player_id from core.draft_pick where is_keeper and player_id is not null
),
drafted as (
    select distinct season, franchise_code, player_id
    from core.draft_pick
    where not is_keeper and player_id is not null and franchise_code is not null
)
select pw.season, pw.week, pw.franchise_code, pw.player_id,
       case when la.type = 'commissioner' then 'other'
            when la.type is not null      then la.type
            when k.player_id is not null  then 'kept'
            when d.player_id is not null  then 'drafted'
            else 'unknown' end                                                          as source
from core.player_week pw
left join latest_add la on la.season = pw.season and la.week = pw.week
                       and la.franchise_code = pw.franchise_code and la.player_id = pw.player_id
left join kept k        on k.season = pw.season and k.franchise_code = pw.franchise_code and k.player_id = pw.player_id
left join drafted d     on d.season = pw.season and d.franchise_code = pw.franchise_code and d.player_id = pw.player_id;

comment on view core.v_player_week_source is
    'How each rostered player-week''s player got onto that roster: trade, waiver, free_agent, kept, drafted, other, unknown.';


-- ---------------------------------------------------------------------
-- Profile of every team-season (2021 on, regular season). Shares are of the
-- starters' Sleeper points, except ground_share (rebuilt stat-line points).
-- ---------------------------------------------------------------------
create or replace view core.v_team_profile as
with reg as materialized (
    select pw.season, pw.week, pw.franchise_code, pw.player_id, pw.points, pw.is_starter,
           case p.position when 'FB' then 'RB' else p.position end as position, p.birth_date
    from core.player_week pw
    join core.season s on s.season = pw.season
    left join core.player p on p.player_id = pw.player_id
    where pw.week < s.playoff_week_start
),
src as materialized (
    select * from core.v_player_week_source
),
starts as materialized (
    select r.*, src.source, sp.passing_points, sp.rushing_points, sp.receiving_points,
           case when r.birth_date is not null and r.position <> 'DEF'
                then (make_date(r.season, 9, 1) - r.birth_date) / 365.25 end as age
    from reg r
    left join src on src.season = r.season and src.week = r.week
                 and src.franchise_code = r.franchise_code and src.player_id = r.player_id
    left join core.player_xref x on x.player_id = r.player_id
    left join core.v_nfl_points_split sp on sp.gsis_id = x.gsis_id and sp.season = r.season and sp.week = r.week
    where r.is_starter
),
team as (
    select season, franchise_code,
           count(distinct week)                                                        as weeks,
           sum(points)                                                                 as starter_points,
           sum(points) filter (where position = 'QB')                                  as qb_points,
           sum(points) filter (where position = 'RB')                                  as rb_points,
           sum(points) filter (where position = 'WR')                                  as wr_points,
           sum(points) filter (where position = 'TE')                                  as te_points,
           sum(points) filter (where position = 'K')                                   as k_points,
           sum(points) filter (where position = 'DEF')                                 as def_points,
           sum(coalesce(passing_points, 0) + coalesce(receiving_points, 0))            as air_points,
           sum(coalesce(rushing_points, 0))                                            as ground_points,
           sum(points) filter (where source = 'drafted')                               as drafted_points,
           sum(points) filter (where source = 'kept')                                  as kept_points,
           sum(points) filter (where source = 'trade')                                 as trade_points,
           sum(points) filter (where source in ('waiver', 'free_agent'))               as pickup_points,
           sum(points) filter (where source in ('other', 'unknown'))                   as other_source_points,
           avg(age)                                                                    as avg_age,
           count(*) filter (where position is distinct from 'DEF')                     as player_starts,
           count(age)                                                                  as starts_with_age
    from starts
    group by season, franchise_code
),
top2 as (
    select season, franchise_code, sum(player_points) filter (where rk <= 2) as top2_points
    from (select season, franchise_code, player_id, sum(points) as player_points,
                 row_number() over (partition by season, franchise_code order by sum(points) desc, player_id) as rk
          from starts
          group by season, franchise_code, player_id) p
    group by season, franchise_code
),
bench as (
    select season, franchise_code, sum(points) filter (where not is_starter) as bench_points
    from reg
    group by season, franchise_code
),
scores as (
    select season, franchise_code, avg(points_for) as score_avg, stddev_pop(points_for) as score_sd
    from core.v_team_game
    where game_type = 'regular' and points_for is not null
    group by season, franchise_code
)
select
    t.season, t.franchise_code, t.weeks, se.status = 'complete'                     as season_complete,
    round(t.starter_points, 2)                                                       as starter_points,
    t.qb_points / nullif(t.starter_points, 0)                                        as qb_share,
    t.rb_points / nullif(t.starter_points, 0)                                        as rb_share,
    t.wr_points / nullif(t.starter_points, 0)                                        as wr_share,
    t.te_points / nullif(t.starter_points, 0)                                        as te_share,
    t.k_points / nullif(t.starter_points, 0)                                         as k_share,
    t.def_points / nullif(t.starter_points, 0)                                       as def_share,
    round(t.air_points, 2)                                                           as air_points,
    round(t.ground_points, 2)                                                        as ground_points,
    t.ground_points / nullif(t.air_points + t.ground_points, 0)                      as ground_share,
    tp.top2_points / nullif(t.starter_points, 0)                                     as top2_share,
    round(sc.score_avg, 2)                                                           as score_avg,
    sc.score_sd / nullif(sc.score_avg, 0)                                            as score_cv,
    coalesce(t.drafted_points, 0) / nullif(t.starter_points, 0)                      as drafted_share,
    coalesce(t.kept_points, 0) / nullif(t.starter_points, 0)                         as kept_share,
    coalesce(t.trade_points, 0) / nullif(t.starter_points, 0)                        as trade_share,
    coalesce(t.pickup_points, 0) / nullif(t.starter_points, 0)                       as pickup_share,
    coalesce(t.other_source_points, 0) / nullif(t.starter_points, 0)                 as other_source_share,
    -- an age only when birth dates cover at least 80% of the team's player starts
    case when t.starts_with_age >= 0.8 * t.player_starts then t.avg_age end          as avg_age,
    t.starts_with_age::numeric / nullif(t.player_starts, 0)                          as age_coverage,
    b.bench_points / nullif(t.weeks, 0)                                              as bench_ppw
from team t
join core.season se on se.season = t.season
left join top2 tp  on tp.season = t.season and tp.franchise_code = t.franchise_code
left join bench b  on b.season = t.season and b.franchise_code = t.franchise_code
left join scores sc on sc.season = t.season and sc.franchise_code = t.franchise_code;

comment on view core.v_team_profile is
    'Every team-season (2021 on, regular season, starters): how it scored (air vs ground, QB share, top-2 share, '
    'weekly swing) and how it was built (drafted / kept / traded / picked-up shares, age, bench points).';


-- ---------------------------------------------------------------------
-- The measures in long form, each against that season's league: average,
-- standard deviation, z (how many standard deviations from the average) and rank.
-- ---------------------------------------------------------------------
create or replace view core.v_team_metric as
with long as (
    select p.season, p.franchise_code, p.season_complete, m.metric, m.value
    from core.v_team_profile p
    cross join lateral (values
        ('ground_share',  p.ground_share::numeric),
        ('qb_share',      p.qb_share::numeric),
        ('te_share',      p.te_share::numeric),
        ('top2_share',    p.top2_share::numeric),
        ('score_cv',      p.score_cv::numeric),
        ('drafted_share', p.drafted_share::numeric),
        ('kept_share',    p.kept_share::numeric),
        ('trade_share',   p.trade_share::numeric),
        ('pickup_share',  p.pickup_share::numeric),
        ('avg_age',       p.avg_age::numeric),
        ('bench_ppw',     p.bench_ppw::numeric)
    ) as m(metric, value)
)
select season, franchise_code, season_complete, metric, value,
       avg(value) over w                                                   as league_avg,
       stddev_pop(value) over w                                            as league_sd,
       (value - avg(value) over w) / nullif(stddev_pop(value) over w, 0)   as z,
       case when value is not null
            then rank() over (partition by season, metric order by value desc nulls last) end as rank_high,
       count(value) over w                                                 as teams
from long
window w as (partition by season, metric);

comment on view core.v_team_metric is
    'Each team-season measure vs the league that season: average, standard deviation, z and rank (1 = highest).';


-- ---------------------------------------------------------------------
-- Every label's score for every team-season: z in the label's direction.
-- qualifies = score >= the label's min_z (1 standard deviation today) and, when the
-- label has one, value >= min_value.
-- ---------------------------------------------------------------------
create or replace view core.v_team_identity_score as
select m.season, m.franchise_code, m.season_complete, i.identity_code, i.name, i.kind, i.metric, i.direction,
       i.min_z, i.sort_order, m.value, m.league_avg, m.league_sd, m.rank_high, m.teams,
       m.z * i.direction                                                   as score,
       coalesce(m.z * i.direction >= i.min_z
                and (i.min_value is null or m.value >= i.min_value), false) as qualifies
from core.v_team_metric m
join core.identity i on i.metric = m.metric;

comment on view core.v_team_identity_score is
    'Every identity label''s score for every team-season (standard deviations from the league average, in the label''s direction).';


-- ---------------------------------------------------------------------
-- One style + one build label per team-season: the qualifying label with the
-- highest score, else the kind's fallback.
-- ---------------------------------------------------------------------
create or replace view core.v_team_identity as
with best as (
    select distinct on (season, franchise_code, kind) *
    from core.v_team_identity_score
    where qualifies
    order by season, franchise_code, kind, score desc, sort_order
),
fallback as (
    select kind, identity_code, name from core.identity where metric is null
)
select
    p.season, p.franchise_code, p.season_complete,
    coalesce(bs.identity_code, fs.identity_code)        as style_code,
    coalesce(bs.name, fs.name)                          as style_name,
    bs.metric                                           as style_metric,
    bs.value                                            as style_value,
    bs.league_avg                                       as style_league_avg,
    round(bs.score, 2)                                  as style_score,
    coalesce(bb.identity_code, fb.identity_code)        as build_code,
    coalesce(bb.name, fb.name)                          as build_name,
    bb.metric                                           as build_metric,
    bb.value                                            as build_value,
    bb.league_avg                                       as build_league_avg,
    round(bb.score, 2)                                  as build_score
from core.v_team_profile p
left join best bs on bs.season = p.season and bs.franchise_code = p.franchise_code and bs.kind = 'style'
left join best bb on bb.season = p.season and bb.franchise_code = p.franchise_code and bb.kind = 'build'
left join fallback fs on fs.kind = 'style'
left join fallback fb on fb.kind = 'build';

comment on view core.v_team_identity is
    'Each team-season''s style label and build label (Phase 10), with the measure and league average behind each.';


-- ---------------------------------------------------------------------
-- All-time identity per franchise: per kind, the label with the highest average
-- season score over the franchise's seasons (2+ seasons with that measure), if the
-- average reaches the label's min_alltime_z. seasons_earned = how many seasons the
-- franchise actually got that label.
-- ---------------------------------------------------------------------
create or replace view core.v_franchise_identity as
with per_label as (
    select franchise_code, identity_code, name, kind, sort_order,
           avg(score) as avg_score, count(*) as seasons
    from core.v_team_identity_score s
    join core.identity i using (identity_code, name, kind, sort_order)
    where score is not null
    group by franchise_code, identity_code, name, kind, sort_order, i.min_alltime_z, i.min_value
    having count(*) >= 2 and avg(score) >= i.min_alltime_z
       and (i.min_value is null or avg(value) >= i.min_value)
),
best as (
    select distinct on (franchise_code, kind) *
    from per_label
    order by franchise_code, kind, avg_score desc, sort_order
),
earned as (
    select franchise_code, style_code as identity_code, count(*) as seasons_earned
    from core.v_team_identity group by franchise_code, style_code
    union all
    select franchise_code, build_code, count(*)
    from core.v_team_identity group by franchise_code, build_code
),
franchises as (
    select franchise_code, count(*) as seasons, min(season) as first_season, max(season) as last_season
    from core.v_team_profile
    group by franchise_code
),
fallback as (
    select kind, identity_code, name from core.identity where metric is null
)
select
    f.franchise_code, f.seasons, f.first_season, f.last_season,
    case when f.seasons >= 2 then coalesce(bs.identity_code, fs.identity_code) end  as style_code,
    case when f.seasons >= 2 then coalesce(bs.name, fs.name) end                    as style_name,
    round(bs.avg_score, 2)                                                          as style_avg_score,
    case when f.seasons >= 2 then coalesce(es.seasons_earned, 0) end               as style_seasons_earned,
    case when f.seasons >= 2 then coalesce(bb.identity_code, fb.identity_code) end  as build_code,
    case when f.seasons >= 2 then coalesce(bb.name, fb.name) end                    as build_name,
    round(bb.avg_score, 2)                                                          as build_avg_score,
    case when f.seasons >= 2 then coalesce(eb.seasons_earned, 0) end               as build_seasons_earned
from franchises f
left join best bs     on bs.franchise_code = f.franchise_code and bs.kind = 'style'
left join best bb     on bb.franchise_code = f.franchise_code and bb.kind = 'build'
left join fallback fs on fs.kind = 'style'
left join fallback fb on fb.kind = 'build'
left join earned es   on es.franchise_code = f.franchise_code
                     and es.identity_code = coalesce(bs.identity_code, fs.identity_code)
left join earned eb   on eb.franchise_code = f.franchise_code
                     and eb.identity_code = coalesce(bb.identity_code, fb.identity_code);

comment on view core.v_franchise_identity is
    'All-time style and build label per franchise (2+ seasons): the label it leaned toward most on average.';
