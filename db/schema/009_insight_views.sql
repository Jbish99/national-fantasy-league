-- =====================================================================
-- db/schema/009_insight_views.sql
-- Phase 4: the "getting better" side of the dashboard (also handy in DBeaver).
--   lineups      best possible lineup vs the one that was started
--   luck         all-play record vs actual record
--   acquisitions what each trade / waiver / free-agent pickup scored for its new team
--   draft        what each pick produced, compared with the rest of its round
--   positions    each team's starters' points by position vs the league average
-- Built on Sleeper's weekly data (core.player_week) and the official record.
--
-- Run after 008. Safe to re-run.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Lineup slots usable each week. Normally the season's slots. But if EVERY team
-- left the same slot empty that week, it wasn't really usable, so it doesn't
-- count. Known case: 2021 week 17, every team's second FLEX is empty in Sleeper's
-- data, though the league never turned it off (Jaden, 2026-11-15). The official
-- scores were scored without it too. One team leaving a slot empty still counts
-- as that team's miss (e.g. GRL's empty K in 2026 week 9).
-- ---------------------------------------------------------------------
create or replace view core.v_lineup_slots_week as
with season_slots as (
    select season, slot, count(*) as season_slots
    from core.roster_slot
    where slot <> 'BN'
    group by season, slot
),
filled as (                                -- how many of each slot each team filled that week
    select season, week, franchise_code, lineup_slot as slot, count(*) as n
    from core.player_week
    where is_starter
    group by season, week, franchise_code, lineup_slot
)
select w.season, w.week, ss.slot, ss.season_slots,
       least(ss.season_slots, coalesce(max(f.n), 0)) as usable_slots
from (select distinct season, week from core.team_week) w
join season_slots ss on ss.season = w.season
left join filled f on f.season = w.season and f.week = w.week and f.slot = ss.slot
group by w.season, w.week, ss.slot, ss.season_slots;

comment on view core.v_lineup_slots_week is
    'Lineup slots per week for the best-lineup math. usable_slots < season_slots when every team left that slot empty that week.';


-- ---------------------------------------------------------------------
-- Lineups. The best possible lineup uses that week's usable slots (above): each
-- fixed slot (QB, RB, WR, TE, K, DEF) takes that position's top scorers, then FLEX
-- takes the best remaining RB/WR/TE. (Filling fixed slots first is always optimal
-- here, because FLEX accepts every position the fixed slots do.) Positions are
-- Sleeper's primary position for each player.
-- ---------------------------------------------------------------------
create or replace view core.v_lineup_players as
with slots as (
    select season, week,
           coalesce(sum(usable_slots) filter (where slot = 'FLEX'), 0) as flex_slots,
           jsonb_object_agg(slot, usable_slots) filter (where slot <> 'FLEX') as fixed_slots   -- {"QB": 1, "RB": 2, ...}
    from core.v_lineup_slots_week
    group by season, week
),
ranked as (
    select pw.season, pw.week, pw.franchise_code, pw.player_id, pw.points, pw.is_starter, pw.lineup_slot,
           coalesce(p.position, '?') as position,
           row_number() over (partition by pw.season, pw.week, pw.franchise_code, coalesce(p.position, '?')
                              order by pw.points desc, pw.player_id)          as position_rank,
           coalesce((sl.fixed_slots ->> coalesce(p.position, '?'))::int, 0)   as position_slots,
           sl.flex_slots
    from core.player_week pw
    join core.player p on p.player_id = pw.player_id
    join slots sl on sl.season = pw.season and sl.week = pw.week
),
flagged as (
    select r.*,
           (r.position_rank <= r.position_slots)                                       as in_fixed_slot,
           (r.position in ('RB', 'WR', 'TE') and r.position_rank > r.position_slots)   as flex_candidate
    from ranked r
),
flex_ranked as (
    select f.*,
           case when f.flex_candidate
                then row_number() over (partition by f.season, f.week, f.franchise_code, f.flex_candidate
                                        order by f.points desc, f.player_id) end      as flex_rank
    from flagged f
)
select season, week, franchise_code, player_id, position, points, is_starter, lineup_slot,
       case when in_fixed_slot then position
            when flex_rank <= flex_slots then 'FLEX' end                              as optimal_slot,
       (in_fixed_slot or coalesce(flex_rank <= flex_slots, false))                    as in_best_lineup
from flex_ranked;

comment on view core.v_lineup_players is
    'Every rostered player-week: started or not, and whether he belonged in the best possible lineup.';


create or replace view core.v_lineup_week as
select
    lp.season,
    lp.week,
    lp.franchise_code,
    (lp.week < s.playoff_week_start)                                         as is_regular_season,
    round(sum(lp.points) filter (where lp.is_starter), 2)                    as actual_points,
    round(sum(lp.points) filter (where lp.in_best_lineup), 2)                as best_points,
    round(coalesce(sum(lp.points) filter (where lp.in_best_lineup), 0)
          - coalesce(sum(lp.points) filter (where lp.is_starter), 0), 2)     as points_left_on_bench,
    round(100 * sum(lp.points) filter (where lp.is_starter)
          / nullif(sum(lp.points) filter (where lp.in_best_lineup), 0), 1)   as lineup_pct,
    count(*) filter (where lp.in_best_lineup and not lp.is_starter)          as wrong_calls
from core.v_lineup_players lp
join core.season s on s.season = lp.season
group by lp.season, lp.week, lp.franchise_code, s.playoff_week_start;

comment on view core.v_lineup_week is
    'Per team-week: points scored, the best possible lineup''s points, points left on the bench, lineup % (actual / best).';


-- ---------------------------------------------------------------------
-- Luck: each regular-season week, every team is compared with every other
-- team's score that week (the "all-play" record). Expected wins = all-play win %
-- x games. Luck = actual wins - expected wins. Uses the official scores.
-- ---------------------------------------------------------------------
create or replace view core.v_all_play as
with week_scores as (
    select season, week, franchise_code, points_for
    from core.v_team_game
    where game_type = 'regular' and points_for is not null
),
compared as (
    select a.season, a.franchise_code,
           count(*) filter (where a.points_for > b.points_for) as all_play_wins,
           count(*) filter (where a.points_for < b.points_for) as all_play_losses,
           count(*) filter (where a.points_for = b.points_for) as all_play_ties
    from week_scores a
    join week_scores b on b.season = a.season and b.week = a.week and b.franchise_code <> a.franchise_code
    group by a.season, a.franchise_code
)
select
    c.season,
    c.franchise_code,
    r.wins, r.losses, r.ties,
    c.all_play_wins, c.all_play_losses, c.all_play_ties,
    round((c.all_play_wins + 0.5 * c.all_play_ties)::numeric
          / nullif(c.all_play_wins + c.all_play_losses + c.all_play_ties, 0), 3)          as all_play_pct,
    round((c.all_play_wins + 0.5 * c.all_play_ties)::numeric
          / nullif(c.all_play_wins + c.all_play_losses + c.all_play_ties, 0)
          * (r.wins + r.losses + r.ties), 2)                                              as expected_wins,
    round(r.wins + 0.5 * r.ties
          - (c.all_play_wins + 0.5 * c.all_play_ties)::numeric
            / nullif(c.all_play_wins + c.all_play_losses + c.all_play_ties, 0)
            * (r.wins + r.losses + r.ties), 2)                                            as luck
from compared c
join core.v_regular_season_record r on r.season = c.season and r.franchise_code = c.franchise_code;

comment on view core.v_all_play is
    'Regular season: actual record vs the record a team would have had playing everyone every week. luck = wins - expected wins.';


-- ---------------------------------------------------------------------
-- Acquisitions: every player added by a completed trade, waiver claim or free-agent
-- pickup, and what he scored for the team that got him that season (from the week
-- of the move until that team added him again, if it ever did).
-- ---------------------------------------------------------------------
create or replace view core.v_acquisitions as
with adds as (
    select t.transaction_id, t.season, t.week, t.type, t.created_at, t.waiver_bid,
           tp.franchise_code, tp.player_id,
           lead(t.week) over (partition by t.season, tp.franchise_code, tp.player_id
                              order by t.week, t.created_at) as next_add_week
    from core.transaction t
    join core.transaction_player tp on tp.transaction_id = t.transaction_id
    where t.status = 'complete'
      and t.type in ('trade', 'waiver', 'free_agent')
      and tp.action = 'add'
)
select
    a.transaction_id,
    a.season,
    a.week,
    a.type,
    a.created_at,
    a.franchise_code,
    a.player_id,
    case when a.type = 'waiver' then a.waiver_bid end                    as faab_bid,
    count(pw.week)                                                       as weeks_rostered,
    count(pw.week) filter (where pw.is_starter)                          as weeks_started,
    coalesce(sum(pw.points), 0)                                          as points_rostered,
    coalesce(sum(pw.points) filter (where pw.is_starter), 0)             as points_started
from adds a
left join core.player_week pw
       on pw.season = a.season and pw.franchise_code = a.franchise_code and pw.player_id = a.player_id
      and pw.week >= a.week
      and (a.next_add_week is null or pw.week < a.next_add_week)
group by a.transaction_id, a.season, a.week, a.type, a.created_at, a.franchise_code, a.player_id, a.waiver_bid;

comment on view core.v_acquisitions is
    'Each player a team added (trade, waiver, free agent) and the points he scored for that team afterwards that season.';


-- ---------------------------------------------------------------------
-- Draft value. Each drafted player's points over that fantasy regular season are
-- rebuilt from his real stat lines, so they count weeks on anyone's roster, or none.
-- A linked player with no stat lines (hurt all year) counts as 0, not blank
-- (only in seasons that have NFL stats loaded).
--
-- Raw points favor QBs (6-point passing TDs), so each player is measured against a
-- replacement-level player at his own position: the Nth-best scorer at that
-- position, where N = how many start league-wide each week
--   (teams x that position's slots, plus its share of the FLEX slots).
-- e.g. 12 teams, QB/RB/RB/WR/WR/TE/FLEX/FLEX -> QB12, RB34, WR34, TE17.
-- Value = points over replacement minus the average of the non-keeper picks in the
-- same round (a round of only keepers compares its keepers with each other).
-- Defenses and the 2020 (ESPN) draft have no rebuilt points, so they get no value.
-- ---------------------------------------------------------------------
-- create OR REPLACE (not drop + create): qa.replacement_level_mismatch (Phase 9) depends on this view,
-- so a drop would fail on every re-run. To change its columns later, drop that qa view first.
create or replace view core.v_draft_value as
with picks as (
    select dp.draft_id, dp.season, dp.round, dp.pick_no, dp.pick_no_is_estimated, dp.franchise_code,
           dp.player_id, coalesce(dp.player_name_as_entered, p.full_name) as player_name, p.position,
           (dp.is_keeper or exists (select 1 from core.keeper k
                                    where k.season = dp.season and k.franchise_code = dp.franchise_code
                                      and k.player_id = dp.player_id))      as is_keeper
    from core.draft_pick dp
    left join core.player p on p.player_id = dp.player_id
),
-- every NFL player's league points for the fantasy regular season (the replacement-level pool)
nfl_season as (
    select lp.gsis_id, lp.season,
           mode() within group (order by case lp.position when 'FB' then 'RB' else lp.position end) as position,
           sum(lp.league_points) as points,
           count(*)              as games
    from core.v_nfl_league_points lp
    join core.season s on s.season = lp.season
    where lp.season_type = 'REG' and lp.week < s.playoff_week_start
    group by lp.gsis_id, lp.season
),
stat_seasons as (                          -- seasons with NFL stat lines loaded at all
    select distinct season from nfl_season
),
slot_counts as (
    select season, slot, count(*) as n
    from core.roster_slot
    where slot <> 'BN'
    group by season, slot
),
starters_per_team as (                     -- e.g. RB 2 + 2 x 2/5 of the FLEX slots = 2.8
    select f.season, f.slot as position,
           f.n + case when f.slot in ('RB', 'WR', 'TE')
                      then coalesce(fl.n, 0) * f.n / nullif(sum(f.n) filter (where f.slot in ('RB', 'WR', 'TE'))
                                                             over (partition by f.season), 0)
                      else 0 end::numeric as per_team
    from slot_counts f
    left join slot_counts fl on fl.season = f.season and fl.slot = 'FLEX'
    where f.slot <> 'FLEX'
),
teams as (
    select season, count(distinct franchise_code) as teams
    from core.team_week
    group by season
),
replacement as (
    select sp.season, sp.position, greatest(round(t.teams * sp.per_team)::int, 1) as replacement_rank
    from starters_per_team sp
    join teams t on t.season = sp.season
),
ranked_pool as (
    select ns.season, ns.position, ns.points,
           row_number() over (partition by ns.season, ns.position order by ns.points desc) as pos_rank
    from nfl_season ns
),
replacement_points as (
    select r.season, r.position, r.replacement_rank,
           coalesce(max(rp.points) filter (where rp.pos_rank = r.replacement_rank),
                    min(rp.points))                                   as replacement_points
    from replacement r
    join ranked_pool rp on rp.season = r.season and rp.position = r.position
    group by r.season, r.position, r.replacement_rank
),
for_drafter as (
    select pw.season, pw.franchise_code, pw.player_id,
           sum(pw.points) filter (where pw.is_starter) as points_started_for_drafter
    from core.player_week pw
    join core.season s on s.season = pw.season
    where pw.week < s.playoff_week_start
    group by pw.season, pw.franchise_code, pw.player_id
),
valued as (
    select pk.*,
           case when x.gsis_id is not null and ss.season is not null then coalesce(ns.points, 0) end         as season_points,
           case when x.gsis_id is not null and ss.season is not null then coalesce(ns.games, 0) end          as games,
           coalesce(fd.points_started_for_drafter, 0)                              as points_started_for_drafter,
           rp.replacement_rank,
           rp.replacement_points,
           case when x.gsis_id is not null and ss.season is not null
                then coalesce(ns.points, 0) - rp.replacement_points end            as points_over_replacement
    from picks pk
    left join core.player_xref x on x.player_id = pk.player_id
    left join stat_seasons ss on ss.season = pk.season
    left join nfl_season ns on ns.gsis_id = x.gsis_id and ns.season = pk.season
    left join replacement_points rp on rp.season = pk.season and rp.position = pk.position
    left join for_drafter fd on fd.season = pk.season and fd.franchise_code = pk.franchise_code
                            and fd.player_id = pk.player_id
),
round_avg as (
    -- the round's non-keeper average; a round made up only of keepers compares its keepers with each other
    select v.*,
           coalesce(avg(v.points_over_replacement) filter (where not v.is_keeper) over (partition by v.season, v.round),
                    avg(v.points_over_replacement) over (partition by v.season, v.round)) as round_avg_raw
    from valued v
)
select draft_id, season, round, pick_no, pick_no_is_estimated, franchise_code, player_id, player_name, position,
       is_keeper, season_points, games, points_started_for_drafter, replacement_rank, replacement_points,
       points_over_replacement,
       round(round_avg_raw, 2)                              as round_average,
       round(points_over_replacement - round_avg_raw, 2)    as value_over_round
from round_avg;

comment on view core.v_draft_value is
    'Each draft pick: rebuilt regular-season points, points over a replacement-level player at his position, '
    'points as a starter for the drafting team, and value vs the other non-keeper picks in his round.';


-- ---------------------------------------------------------------------
-- Positions: each team's starters' points per regular-season week by position
-- (a FLEX starter counts at his own position), vs the league average that season.
-- ---------------------------------------------------------------------
create or replace view core.v_position_strength as
with started as (
    select pw.season, pw.franchise_code, coalesce(p.position, '?') as position, sum(pw.points) as points
    from core.player_week pw
    join core.player p on p.player_id = pw.player_id
    join core.season s on s.season = pw.season
    where pw.is_starter and pw.week < s.playoff_week_start
    group by pw.season, pw.franchise_code, coalesce(p.position, '?')
),
team_weeks as (
    select tw.season, tw.franchise_code, count(*) as weeks
    from core.team_week tw
    join core.season s on s.season = tw.season
    where tw.week < s.playoff_week_start
    group by tw.season, tw.franchise_code
)
select st.season, st.franchise_code, st.position,
       round(st.points, 2)                                                           as started_points,
       round(st.points / tw.weeks, 2)                                                as points_per_week,
       round(avg(st.points / tw.weeks) over (partition by st.season, st.position), 2) as league_average,
       round(st.points / tw.weeks
             - avg(st.points / tw.weeks) over (partition by st.season, st.position), 2) as vs_league
from started st
join team_weeks tw on tw.season = st.season and tw.franchise_code = st.franchise_code;

comment on view core.v_position_strength is
    'Regular season: starters'' points per week by position for each team, and how far above/below the league average.';
