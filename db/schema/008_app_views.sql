-- =====================================================================
-- db/schema/008_app_views.sql
-- Phase 4: views the Streamlit app reads (also handy in DBeaver).
-- All built on the OFFICIAL game record (core.game / core.v_team_game).
--
-- Run after 007. Safe to re-run.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Each franchise's final place each season, from the placement games:
-- Fantasy Bowl = 1st/2nd, 3rd Place Bowl = 3rd/4th, ... 11th Place Bowl = 11th/12th.
-- The 2022 co-champions (Fantasy Bowl with no winner) are both 1st.
-- ---------------------------------------------------------------------
create or replace view core.v_season_finish as
select
    t.season,
    t.franchise_code,
    case
        when t.designation = 'Fantasy Bowl' then case when t.result = 'L' then 2 else 1 end
        else substring(t.designation from '^(\d+)')::int + case when t.result = 'L' then 1 else 0 end
    end            as place,
    t.designation  as placement_game
from core.v_team_game t
where t.designation = 'Fantasy Bowl'
   or t.designation ~ '^\d+(st|nd|rd|th) Place Bowl$';

comment on view core.v_season_finish is 'Final place per franchise per season, from the Fantasy Bowl and the place bowls.';


-- ---------------------------------------------------------------------
-- One row per franchise per season: regular-season record, playoffs, finish.
-- regular_season_rank orders by record, then points for (the league's real
-- tiebreakers may differ; this is for display).
-- ---------------------------------------------------------------------
create or replace view core.v_season_standings as
with regular as (
    select season, franchise_code, wins, losses, ties, points_for, points_against,
           wins + losses + ties as games
    from core.v_regular_season_record
),
playoffs as (
    select season, franchise_code,
           count(*) filter (where result = 'W') as playoff_wins,
           count(*) filter (where result = 'L') as playoff_losses
    from core.v_team_game
    where game_type = 'playoff'
    group by season, franchise_code
)
select
    fs.season,
    fs.franchise_code,
    f.location || ' ' || f.nickname                                    as franchise,
    fs.team_name,
    o.display_name                                                     as owner,
    coalesce(r.wins, 0)                                                as wins,
    coalesce(r.losses, 0)                                              as losses,
    coalesce(r.ties, 0)                                                as ties,
    r.points_for,
    r.points_against,
    round((r.wins + 0.5 * r.ties)::numeric / nullif(r.games, 0), 3)    as win_pct,
    round(r.points_for / nullif(r.games, 0), 2)                        as points_per_game,
    rank() over (partition by fs.season
                 order by (coalesce(r.wins, 0) + 0.5 * coalesce(r.ties, 0)) desc, r.points_for desc nulls last)
                                                                       as regular_season_rank,
    (p.franchise_code is not null)                                     as made_playoffs,
    coalesce(p.playoff_wins, 0)                                        as playoff_wins,
    coalesce(p.playoff_losses, 0)                                      as playoff_losses,
    sf.place                                                           as final_place,
    coalesce(sf.place = 1, false)                                      as champion
from core.franchise_season fs
join core.franchise f            on f.franchise_code = fs.franchise_code
left join core.owner o           on o.owner_id = fs.owner_id
left join regular r              on r.season = fs.season and r.franchise_code = fs.franchise_code
left join playoffs p             on p.season = fs.season and p.franchise_code = fs.franchise_code
left join core.v_season_finish sf on sf.season = fs.season and sf.franchise_code = fs.franchise_code;

comment on view core.v_season_standings is
    'Per franchise per season: regular-season record and points, playoff record, final place, champion flag.';


-- ---------------------------------------------------------------------
-- All-time totals per franchise (completed and current seasons).
-- ---------------------------------------------------------------------
create or replace view core.v_all_time_standings as
select
    franchise_code,
    min(franchise)                                                            as franchise,
    count(*)                                                                  as seasons,
    min(season)                                                               as first_season,
    max(season)                                                               as last_season,
    sum(wins)                                                                 as wins,
    sum(losses)                                                               as losses,
    sum(ties)                                                                 as ties,
    round((sum(wins) + 0.5 * sum(ties))::numeric / nullif(sum(wins + losses + ties), 0), 3) as win_pct,
    sum(points_for)                                                           as points_for,
    sum(points_against)                                                       as points_against,
    round(sum(points_for) / nullif(sum(wins + losses + ties), 0), 2)          as points_per_game,
    count(*) filter (where made_playoffs)                                     as playoff_appearances,
    sum(playoff_wins)                                                         as playoff_wins,
    sum(playoff_losses)                                                       as playoff_losses,
    count(*) filter (where final_place = 1)                                   as championships,
    count(*) filter (where final_place = 2)                                   as runner_ups,
    min(final_place)                                                          as best_finish,
    round(avg(final_place), 1)                                                as avg_finish
from core.v_season_standings
group by franchise_code;

comment on view core.v_all_time_standings is 'All-time regular-season record, points, playoff record and titles per franchise.';
