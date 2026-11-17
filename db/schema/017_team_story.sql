-- =====================================================================
-- db/schema/017_team_story.sql
-- Phase 10b: football formations + the facts behind the scouting reports.
--
-- Decisions (Jaden, 2026-11-16):
--   * Every team-season gets a base FORMATION from what it put in its FLEX spots
--     (every season 2021-2026 has 2 FLEX spots), like a real team's base offense:
--         WR + WR      -> Four-Wide Spread      RB + WR -> Pro Set
--         RB + RB      -> Power I               a TE in one FLEX -> Heavy Set
--         TE + TE      -> Jumbo
--     The base formation is the one it lined up in for MORE THAN HALF of its counted
--     weeks; otherwise "Multiple" (no base set). Names and descriptions live in
--     db/seed/001_reference_data.sql (table core.formation): rename there.
--   * Counted weeks: regular season (same as the identity labels), and only weeks where
--     every FLEX spot held a RB, WR or TE, so a week a team left a FLEX empty doesn't count.
--   * All-time: the formation a franchise lined up in for more than half of all its
--     counted weeks (2+ seasons), else Multiple.
--   * The scouting reports (app/story.py) are written from these views plus
--     core.v_team_identity / core.v_team_metric (016) and core.v_season_standings.
--
-- For more than 2 FLEX spots (or 1) the same rule still works: any 2+ TEs = Jumbo,
-- 1 TE = Heavy Set, else more RBs than WRs = Power I, more WRs = Four-Wide Spread,
-- an even split = Pro Set.
--
-- New here: core.formation (table), core.v_team_week_formation, core.v_team_formation,
-- core.v_franchise_formation, core.v_team_player_season.
--
-- Run after 016. Safe to re-run.
-- =====================================================================


-- ---------------------------------------------------------------------
-- The formations. Rows are in db/seed/001_reference_data.sql (edit names there).
-- formation_code values are fixed by the rule in core.v_team_week_formation
-- (four_wide, pro_set, power_i, heavy_set, jumbo) plus one fallback (is_fallback).
-- ---------------------------------------------------------------------
create table if not exists core.formation (
    formation_code  text      primary key,
    name            text      not null,
    flex_set        text,                          -- what's in the FLEX spots, e.g. 'WR + WR'
    description     text      not null,
    sort_order      smallint  not null default 0,
    is_fallback     boolean   not null default false
);
create unique index if not exists ux_formation_fallback on core.formation (is_fallback) where is_fallback;

comment on table core.formation is
    'Football formations (Phase 10b) named after what a team put in its FLEX spots. Rows in db/seed/001.';


-- ---------------------------------------------------------------------
-- Every regular-season team-week: how many RBs (FB counts as RB), WRs and TEs it
-- started in its FLEX spots, and the formation that makes. formation_code is null
-- when a FLEX spot was empty (or held someone without a RB/WR/TE position).
-- ---------------------------------------------------------------------
create or replace view core.v_team_week_formation as
with flex_slots as (
    select season, count(*) as flex_slots
    from core.roster_slot
    where slot = 'FLEX'
    group by season
),
flex as (
    select pw.season, pw.week, pw.franchise_code,
           count(*) filter (where p.position in ('RB', 'FB'))  as flex_rb,
           count(*) filter (where p.position = 'WR')           as flex_wr,
           count(*) filter (where p.position = 'TE')           as flex_te,
           count(*)                                            as flex_filled
    from core.player_week pw
    left join core.player p on p.player_id = pw.player_id
    where pw.lineup_slot = 'FLEX'
    group by pw.season, pw.week, pw.franchise_code
),
weeks as (
    select tw.season, tw.week, tw.franchise_code, fs.flex_slots,
           coalesce(f.flex_rb, 0) as flex_rb, coalesce(f.flex_wr, 0) as flex_wr,
           coalesce(f.flex_te, 0) as flex_te, coalesce(f.flex_filled, 0) as flex_filled
    from core.team_week tw
    join core.season s     on s.season = tw.season
    join flex_slots fs     on fs.season = tw.season
    left join flex f       on f.season = tw.season and f.week = tw.week and f.franchise_code = tw.franchise_code
    where tw.week < s.playoff_week_start
)
select season, week, franchise_code, flex_slots, flex_rb, flex_wr, flex_te, flex_filled,
       case when flex_rb + flex_wr + flex_te = flex_slots then
           case when flex_te >= 2      then 'jumbo'
                when flex_te = 1       then 'heavy_set'
                when flex_rb > flex_wr then 'power_i'
                when flex_wr > flex_rb then 'four_wide'
                else 'pro_set' end
       end                                                       as formation_code
from weeks;

comment on view core.v_team_week_formation is
    'Each regular-season team-week''s FLEX spots (RB / WR / TE counts) and the formation they make.';


-- ---------------------------------------------------------------------
-- Base formation per team-season: the formation it lined up in for more than half of
-- its counted weeks, else the fallback (Multiple). top_* = its most-used formation
-- either way (for "most often Pro Set"). *_weeks = weeks in each formation.
-- league_share = share of the whole league's counted team-weeks that season in top_code.
-- ---------------------------------------------------------------------
create or replace view core.v_team_formation as
with by_formation as (
    select season, franchise_code, formation_code, count(*) as weeks
    from core.v_team_week_formation
    where formation_code is not null
    group by season, franchise_code, formation_code
),
team as (
    select season, franchise_code,
           count(*)                                          as weeks_played,
           count(*) filter (where formation_code is not null) as weeks_counted
    from core.v_team_week_formation
    group by season, franchise_code
),
league as (
    select season, formation_code,
           sum(weeks)::numeric / sum(sum(weeks)) over (partition by season) as league_share
    from by_formation
    group by season, formation_code
),
top as (
    select distinct on (b.season, b.franchise_code) b.season, b.franchise_code, b.formation_code, b.weeks
    from by_formation b
    left join core.formation f on f.formation_code = b.formation_code
    order by b.season, b.franchise_code, b.weeks desc, f.sort_order nulls last, b.formation_code
),
mix as (
    select season, franchise_code,
           coalesce(sum(weeks) filter (where formation_code = 'four_wide'), 0) as four_wide_weeks,
           coalesce(sum(weeks) filter (where formation_code = 'pro_set'), 0)   as pro_set_weeks,
           coalesce(sum(weeks) filter (where formation_code = 'power_i'), 0)   as power_i_weeks,
           coalesce(sum(weeks) filter (where formation_code = 'heavy_set'), 0) as heavy_set_weeks,
           coalesce(sum(weeks) filter (where formation_code = 'jumbo'), 0)     as jumbo_weeks
    from by_formation
    group by season, franchise_code
),
fallback as (
    select formation_code, name from core.formation where is_fallback
),
picked as (
    select t.season, t.franchise_code, t.weeks_played, t.weeks_counted,
           -- base formation = used in MORE than half of the counted weeks (change 0.5 here to tune)
           case when tp.weeks > 0.5 * t.weeks_counted then tp.formation_code
                when t.weeks_counted > 0 then (select formation_code from fallback) end as formation_code,
           tp.formation_code as top_code, tp.weeks as top_weeks
    from team t
    left join top tp on tp.season = t.season and tp.franchise_code = t.franchise_code
)
select p.season, p.franchise_code, se.status = 'complete'                           as season_complete,
       p.weeks_played, p.weeks_counted,
       p.formation_code, f.name                                                    as formation_name,
       coalesce(f.is_fallback, false)                                              as is_fallback,
       p.top_code, ft.name                                                         as top_name, p.top_weeks,
       p.top_weeks::numeric / nullif(p.weeks_counted, 0)                           as top_share,
       lg.league_share                                                             as top_league_share,
       m.four_wide_weeks, m.pro_set_weeks, m.power_i_weeks, m.heavy_set_weeks, m.jumbo_weeks
from picked p
join core.season se       on se.season = p.season
left join core.formation f  on f.formation_code = p.formation_code
left join core.formation ft on ft.formation_code = p.top_code
left join league lg       on lg.season = p.season and lg.formation_code = p.top_code
left join mix m           on m.season = p.season and m.franchise_code = p.franchise_code;

comment on view core.v_team_formation is
    'Each team-season''s base formation (more than half its counted weeks, else Multiple) and its weeks in each formation.';


-- ---------------------------------------------------------------------
-- All-time formation per franchise (2+ seasons): more than half of all its counted
-- weeks, else Multiple. seasons_base = seasons that formation was its base.
-- ---------------------------------------------------------------------
create or replace view core.v_franchise_formation as
with by_formation as (
    select franchise_code, formation_code, count(*) as weeks
    from core.v_team_week_formation
    where formation_code is not null
    group by franchise_code, formation_code
),
team as (
    select franchise_code,
           count(distinct season)                             as seasons,
           count(*) filter (where formation_code is not null) as weeks_counted
    from core.v_team_week_formation
    group by franchise_code
),
top as (
    select distinct on (b.franchise_code) b.franchise_code, b.formation_code, b.weeks
    from by_formation b
    left join core.formation f on f.formation_code = b.formation_code
    order by b.franchise_code, b.weeks desc, f.sort_order nulls last, b.formation_code
),
mix as (
    select franchise_code,
           coalesce(sum(weeks) filter (where formation_code = 'four_wide'), 0) as four_wide_weeks,
           coalesce(sum(weeks) filter (where formation_code = 'pro_set'), 0)   as pro_set_weeks,
           coalesce(sum(weeks) filter (where formation_code = 'power_i'), 0)   as power_i_weeks,
           coalesce(sum(weeks) filter (where formation_code = 'heavy_set'), 0) as heavy_set_weeks,
           coalesce(sum(weeks) filter (where formation_code = 'jumbo'), 0)     as jumbo_weeks
    from by_formation
    group by franchise_code
),
base_seasons as (
    select franchise_code, formation_code, count(*) as seasons_base
    from core.v_team_formation
    group by franchise_code, formation_code
),
picked as (
    select t.franchise_code, t.seasons, t.weeks_counted,
           case when t.seasons < 2 or t.weeks_counted = 0 then null
                when tp.weeks > 0.5 * t.weeks_counted then tp.formation_code
                else (select formation_code from core.formation where is_fallback) end as formation_code,
           tp.formation_code as top_code, tp.weeks as top_weeks
    from team t
    left join top tp on tp.franchise_code = t.franchise_code
)
select p.franchise_code, p.seasons, p.weeks_counted,
       p.formation_code, f.name                                as formation_name,
       coalesce(f.is_fallback, false)                          as is_fallback,
       coalesce(bs.seasons_base, 0)                            as seasons_base,
       p.top_code, ft.name                                     as top_name, p.top_weeks,
       p.top_weeks::numeric / nullif(p.weeks_counted, 0)       as top_share,
       m.four_wide_weeks, m.pro_set_weeks, m.power_i_weeks, m.heavy_set_weeks, m.jumbo_weeks
from picked p
left join core.formation f  on f.formation_code = p.formation_code
left join core.formation ft on ft.formation_code = p.top_code
left join base_seasons bs   on bs.franchise_code = p.franchise_code and bs.formation_code = p.formation_code
left join mix m             on m.franchise_code = p.franchise_code;

comment on view core.v_franchise_formation is
    'All-time base formation per franchise (2+ seasons): more than half of all its counted weeks, else Multiple.';


-- ---------------------------------------------------------------------
-- Each team-season's starters (regular season): points and starts per player, split by
-- how he got onto that roster (core.v_player_week_source). The scouting reports use it
-- to name names: the team's best player, its QB, its top 2, its best trade / pickup / keeper.
-- ---------------------------------------------------------------------
create or replace view core.v_team_player_season as
select pw.season, pw.franchise_code, pw.player_id, p.full_name,
       case p.position when 'FB' then 'RB' else p.position end as position,
       coalesce(src.source, 'unknown')                         as source,
       count(*)                                                as starts,
       round(sum(pw.points), 2)                                as points
from core.player_week pw
join core.season s  on s.season = pw.season
join core.player p  on p.player_id = pw.player_id
left join core.v_player_week_source src
       on src.season = pw.season and src.week = pw.week
      and src.franchise_code = pw.franchise_code and src.player_id = pw.player_id
where pw.is_starter and pw.week < s.playoff_week_start
group by pw.season, pw.franchise_code, pw.player_id, p.full_name, p.position, coalesce(src.source, 'unknown');

comment on view core.v_team_player_season is
    'Each team-season''s regular-season starters: starts and points per player and roster source (for the scouting reports).';
