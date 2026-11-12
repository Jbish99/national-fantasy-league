-- =====================================================================
-- db/checks/003_points_investigation.sql
-- Which Sleeper number matches the official workbook: Sleeper's team
-- total ('points') or the sum of the starters' points?
--
-- Needs: raw.sleeper_response (python -m etl.pull_sleeper)
--        raw.workbook_row     (python -m etl.load_workbook)
-- Works on the raw data only, so the core tables don't need to be loaded.
-- Run the whole file in DBeaver (Alt+X). Each query opens its own result tab.
-- =====================================================================

create schema if not exists qa;

-- One row per team-week that appears in both Sleeper and the workbook.
-- (Sleeper's unofficial week-17 5th/7th place games aren't in the workbook, so they drop out.)
create or replace view qa.sleeper_points_vs_workbook as
with roster_map (season_from, season_to, roster_id, franchise_code) as (values
    (2021, 2099, 1, 'DET'), (2021, 2099, 2, 'GRB'), (2021, 2099, 3, 'FAR'), (2021, 2099, 4, 'VHC'),
    (2021, 2099, 5, 'OAK'), (2021, 2099, 6, 'UPN'), (2021, 2099, 7, 'FLT'), (2021, 2099, 8, 'OAP'),
    (2021, 2021, 9, 'MIS'), (2022, 2099, 9, 'GRR'), (2021, 2099, 10, 'STO'), (2021, 2099, 11, 'SDP'),
    (2021, 2099, 12, 'GRL')
),
city_map (city, franchise_code) as (values
    ('Detroit', 'DET'), ('Farmington', 'FAR'), ('Flint', 'FLT'), ('Grand Blanc', 'GRB'),
    ('Grand Rapids', 'GRR'), ('Great Lakes', 'GRL'), ('Mississippi', 'MIS'), ('Oak Park', 'OAP'),
    ('Oakland', 'OAK'), ('Sand Point', 'SDP'), ('Stonebrook', 'STO'), ('Up North N', 'UPN'),
    ('Vehicle City', 'VHC')
),
sleeper as (
    select
        r.season,
        r.week,
        rm.franchise_code,
        (m ->> 'points')::numeric        as sleeper_total,
        (m ->> 'custom_points')::numeric as custom_points,
        (select sum(v::numeric)
           from jsonb_array_elements_text(m -> 'starters_points') as v)            as starters_sum,
        (select sum((m -> 'players_points' ->> s)::numeric)
           from jsonb_array_elements_text(m -> 'starters') as s)                   as starters_sum_from_players_points
    from raw.v_sleeper_latest r
    cross join jsonb_array_elements(r.payload) as m
    join roster_map rm
      on rm.roster_id = (m ->> 'roster_id')::int
     and r.season between rm.season_from and rm.season_to
    where r.endpoint = 'matchups'
),
workbook as (
    select (w.data ->> 'Year')::int as season, (w.data ->> 'Week')::int as week,
           c.franchise_code, (w.data ->> 'Team 1 Score')::numeric as workbook_score
    from raw.workbook_row w
    join city_map c on c.city = w.data ->> 'Team 1'
    where w.sheet = 'Scores'
    union all
    select (w.data ->> 'Year')::int, (w.data ->> 'Week')::int,
           c.franchise_code, (w.data ->> 'Team 2 Score')::numeric
    from raw.workbook_row w
    join city_map c on c.city = w.data ->> 'Team 2'
    where w.sheet = 'Scores'
)
select
    s.season,
    s.week,
    s.franchise_code,
    s.sleeper_total,
    s.starters_sum,
    wb.workbook_score,
    s.sleeper_total - s.starters_sum   as total_minus_starters,
    wb.workbook_score - s.starters_sum as workbook_minus_starters,
    wb.workbook_score - s.sleeper_total as workbook_minus_total,
    case
        when abs(wb.workbook_score - s.starters_sum) < 0.01
         and abs(wb.workbook_score - s.sleeper_total) < 0.01 then 'both match'
        when abs(wb.workbook_score - s.starters_sum) < 0.01  then 'starters sum matches'
        when abs(wb.workbook_score - s.sleeper_total) < 0.01 then 'total matches'
        else 'neither matches'
    end                                 as workbook_matches,
    s.starters_sum_from_players_points,
    s.custom_points
from sleeper s
join workbook wb
  on wb.season = s.season
 and wb.week = s.week
 and wb.franchise_code = s.franchise_code;

comment on view qa.sleeper_points_vs_workbook is
    'Per team-week: Sleeper total vs sum of starters vs the official workbook score. For investigating 2023.';


-- 1. Season by season: how often each Sleeper number matches the workbook.
select
    season,
    count(*)                                                          as team_weeks,
    count(*) filter (where abs(total_minus_starters) >= 0.01)         as total_not_equal_starters,
    count(*) filter (where workbook_matches = 'both match')           as both_match,
    count(*) filter (where workbook_matches = 'starters sum matches') as only_starters_match,
    count(*) filter (where workbook_matches = 'total matches')        as only_total_matches,
    count(*) filter (where workbook_matches = 'neither matches')      as neither_matches,
    round(avg(total_minus_starters), 2)                               as avg_total_minus_starters,
    count(*) filter (where custom_points is not null)                 as custom_points_set
from qa.sleeper_points_vs_workbook
group by season
order by season;


-- 2. 2023 week by week: does the gap show up every week, or start at some point?
select
    week,
    count(*)                                                           as teams,
    round(avg(total_minus_starters), 2)                                as avg_total_minus_starters,
    min(total_minus_starters)                                          as smallest_gap,
    max(total_minus_starters)                                          as biggest_gap,
    count(*) filter (where workbook_matches in ('both match', 'starters sum matches')) as workbook_equals_starters
from qa.sleeper_points_vs_workbook
where season = 2023
group by week
order by week;


-- 3. Every team-week where the workbook matches NEITHER Sleeper number (all seasons).
--    These are the ones to eyeball: workbook typos, stat corrections, or something else.
select season, week, franchise_code, workbook_score, starters_sum, sleeper_total,
       workbook_minus_starters, workbook_minus_total
from qa.sleeper_points_vs_workbook
where workbook_matches = 'neither matches'
order by season, week, franchise_code;
