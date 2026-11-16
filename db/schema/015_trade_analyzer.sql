-- =====================================================================
-- db/schema/015_trade_analyzer.sql
-- Phase 9: the historical trade analyzer.
--
-- How a trade is judged (decisions by Jaden, 2026-11-16):
--   * Each team is credited with what the assets it RECEIVED produced for it:
--     points over replacement in the weeks it STARTED them. Replacement = the
--     Nth-best player at his position that season, N = starters league-wide
--     (same levels as the Draft page), spread evenly over the fantasy regular
--     season's weeks. Regular season and playoff games (game_type 'playoff')
--     count in ONE total; consolation games and weeks after elimination don't.
--   * A player counts from the trade until he leaves that roster: the rest of
--     that season, then every later season the team kept him (core.keeper or a
--     keeper pick), until it drops or trades him.
--   * A traded draft pick = the player drafted with it, counted the same way from
--     the draft on. Only the trade that LAST moved the pick gets him; earlier
--     moves of the same pick show as 'traded_on' (value 0 there). A pick used
--     for a keeper brings no new player (value 0).
--   * FAAB in trades: listed in dollars, NOT counted in the value.
--   * net = value received - value given (what the assets a team sent produced
--     for the teams that got them). A team won its side when net >= 1 point,
--     lost it when net <= -1, else even. The trade's winner = highest net (>= 1).
--
-- Within the trade season, an asset counts exactly the weeks core.v_acquisitions
-- counts (from the trade week until that team adds him again), so the two agree
-- (qa.trade_vs_acquisitions).
--
-- New here:
--   core.draft_slot           table: whose slot each draft column was (load_core, drafts step)
--   core.v_draft_slot_owner   draft_slot, else worked out from the picks themselves
--   core.v_traded_pick        every traded pick -> final owner and the player drafted with it
--   core.v_replacement_level  replacement level per season and position (+ DEF), and per week
--   core.v_trade_asset        one row per thing a team received in a trade
--   core.v_trade_stint        each asset's player, season by season, while on that roster
--   core.v_trade_asset_week   every player-week that counts for an asset, with points over replacement
--   core.v_trade_asset_value  totals per asset
--   core.v_trade_side         per trade and team: received, given, net, before/after, season outcome
--   core.v_trade              per trade: teams, winner, margin
--
-- Run after 014. Safe to re-run.
-- =====================================================================


-- ---------------------------------------------------------------------
-- Draft slots. In a snake draft every team owns one column (draft_slot). When a
-- pick is traded, the new owner makes it in the ORIGINAL team's column, so
-- (round, original team's slot) finds the pick. Checked on the 2025 sample:
-- pick 8 (slot 8 = roster 7's) was made by roster 5, matching the traded pick
-- "roster 7's 2025 1st -> roster 5".
-- ---------------------------------------------------------------------
create table if not exists core.draft_slot (
    draft_id        text     not null references core.draft on delete cascade,
    draft_slot      smallint not null,
    franchise_code  text     not null references core.franchise,
    source          text     not null check (source in ('slot_to_roster_id', 'draft_order')),
    primary key (draft_id, draft_slot)
);
comment on table core.draft_slot is
    'Which franchise each draft column (draft_slot) belonged to before any trades, from Sleeper''s '
    'slot_to_roster_id or draft_order. Loaded by load_core (drafts step).';


create or replace view core.v_draft_slot_owner as
with from_picks as (       -- fallback: the column each team made most of its picks from (own picks + keepers)
    select draft_id, franchise_code, mode() within group (order by draft_slot) as draft_slot
    from core.draft_pick
    where draft_slot is not null
    group by draft_id, franchise_code
)
select ds.draft_id, d.season, ds.draft_slot, ds.franchise_code, ds.source
from core.draft_slot ds
join core.draft d on d.draft_id = ds.draft_id
union all
select fp.draft_id, d.season, fp.draft_slot, fp.franchise_code, 'picks'
from from_picks fp
join core.draft d on d.draft_id = fp.draft_id
where d.source = 'sleeper'
  and not exists (select 1 from core.draft_slot ds where ds.draft_id = fp.draft_id);

comment on view core.v_draft_slot_owner is
    'Each team''s own draft column per Sleeper draft (core.draft_slot; else the column it made most picks from).';


-- ---------------------------------------------------------------------
-- Traded picks: one row per pick that changed hands in a completed trade.
-- final_owner_code = who got it in the newest trade that moved it.
-- status: drafted         the final owner used it on a new player
--         keeper          the final owner used it to place a keeper (no new player)
--         not_drafted_yet that season's draft hasn't happened
--         no_match        no pick found in that round + column (see qa.traded_pick_unmatched)
--         made_by_other   the pick in that spot was made by someone else (see qa.traded_pick_unmatched)
-- ---------------------------------------------------------------------
create or replace view core.v_traded_pick as
with moves as (
    select tp.pick_season, tp.pick_round, tp.original_code, tp.to_code, t.transaction_id,
           row_number() over (partition by tp.pick_season, tp.pick_round, tp.original_code
                              order by t.created_at desc, t.transaction_id desc)        as newest_first,
           count(*) over (partition by tp.pick_season, tp.pick_round, tp.original_code)  as times_traded
    from core.transaction_pick tp
    join core.transaction t on t.transaction_id = tp.transaction_id
    where t.type = 'trade' and t.status = 'complete'
),
final as (
    select pick_season, pick_round, original_code, to_code as final_owner_code,
           transaction_id as final_transaction_id, times_traded
    from moves
    where newest_first = 1
),
season_draft as (
    select s.season, d.draft_id, d.started_at,
           exists (select 1 from core.draft_pick dp where dp.draft_id = d.draft_id) as has_picks
    from core.season s
    join core.draft d on d.draft_id = s.sleeper_draft_id
),
matched as (
    select f.*, sd.draft_id, sd.has_picks, sd.started_at as draft_started_at,
           so.draft_slot, so.source as slot_source,
           dp.pick_no, dp.franchise_code as made_by_code, dp.player_id,
           (dp.is_keeper or exists (select 1 from core.keeper k
                                    where k.season = dp.season and k.franchise_code = dp.franchise_code
                                      and k.player_id = dp.player_id))  as is_keeper
    from final f
    left join season_draft sd on sd.season = f.pick_season
    left join core.v_draft_slot_owner so on so.draft_id = sd.draft_id and so.franchise_code = f.original_code
    left join core.draft_pick dp on dp.draft_id = sd.draft_id and dp.round = f.pick_round
                                and dp.draft_slot = so.draft_slot
)
select
    pick_season, pick_round, original_code, final_owner_code, final_transaction_id, times_traded,
    draft_id, draft_slot, slot_source, pick_no, made_by_code, player_id, coalesce(is_keeper, false) as is_keeper,
    case
        when draft_id is null or not has_picks                then 'not_drafted_yet'
        when pick_no is null                                  then 'no_match'
        when made_by_code is distinct from final_owner_code   then 'made_by_other'
        when is_keeper                                        then 'keeper'
        else 'drafted'
    end                                                       as status,
    draft_started_at
from matched;

comment on view core.v_traded_pick is
    'Every traded pick: its final owner and the player drafted with it (status drafted / keeper / not_drafted_yet / no_match / made_by_other).';


-- ---------------------------------------------------------------------
-- Replacement levels: the same numbers as core.v_draft_value (checked by
-- qa.replacement_level_mismatch), plus DEF (rebuilt DEF points, Phase 7b).
-- per_week = replacement_points / the fantasy regular-season weeks with NFL stats
-- so far (14 in a finished season; fewer while a season is in progress).
-- ---------------------------------------------------------------------
create or replace view core.v_replacement_level as
with pool as (             -- every NFL player's league points over the fantasy regular season
    select lp.gsis_id::text as id, lp.season,
           mode() within group (order by case lp.position when 'FB' then 'RB' else lp.position end) as position,
           sum(lp.league_points) as points
    from core.v_nfl_league_points lp
    join core.season s on s.season = lp.season
    where lp.season_type = 'REG' and lp.week < s.playoff_week_start
    group by lp.gsis_id, lp.season
    union all
    select d.sleeper_def_id, d.season, 'DEF', sum(d.league_points)
    from core.v_def_league_points d
    join core.season s on s.season = d.season
    where d.week < s.playoff_week_start and d.sleeper_def_id is not null
    group by d.sleeper_def_id, d.season
),
weeks as (
    select n.season, count(distinct n.week) as weeks
    from core.nfl_player_week n
    join core.season s on s.season = n.season
    where n.season_type = 'REG' and n.week < s.playoff_week_start
    group by n.season
),
slot_counts as (
    select season, slot, count(*) as n
    from core.roster_slot
    where slot <> 'BN'
    group by season, slot
),
starters_per_team as (     -- e.g. RB 2 + 2 x 2/5 of the FLEX slots = 2.8
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
    select season, position, points,
           row_number() over (partition by season, position order by points desc) as pos_rank
    from pool
),
replacement_points as (
    select r.season, r.position, r.replacement_rank,
           coalesce(max(rp.points) filter (where rp.pos_rank = r.replacement_rank), min(rp.points)) as replacement_points
    from replacement r
    join ranked_pool rp on rp.season = r.season and rp.position = r.position
    group by r.season, r.position, r.replacement_rank
)
select rp.season, rp.position, rp.replacement_rank, rp.replacement_points, w.weeks,
       rp.replacement_points / nullif(w.weeks, 0) as per_week
from replacement_points rp
left join weeks w on w.season = rp.season;

comment on view core.v_replacement_level is
    'Replacement level per season and position (Nth best over the fantasy regular season, N = starters league-wide) '
    'and per week. Same as core.v_draft_value''s, plus DEF.';


-- ---------------------------------------------------------------------
-- Trade assets: one row per thing a team received in a completed trade.
--   player  the player added (sender = the team that dropped him in the trade)
--   pick    a draft pick; player_id = the player drafted with it (or the keeper placed with it)
--   faab    FAAB dollars (listed only; never valued)
-- value_from_*: where the asset starts counting (null = it never counts).
-- ---------------------------------------------------------------------
create or replace view core.v_trade_asset as
with trades as (
    select transaction_id, season, week, created_at
    from core.transaction
    where type = 'trade' and status = 'complete'
)
select
    t.transaction_id || ':player:' || a.player_id                         as asset_id,
    t.transaction_id, t.season, t.week, t.created_at,
    'player'::text                                                         as asset_type,
    a.franchise_code                                                       as receiver_code,
    d.franchise_code                                                       as sender_code,
    a.player_id,
    null::smallint                                                         as pick_season,
    null::smallint                                                         as pick_round,
    null::text                                                             as pick_original_code,
    null::text                                                             as pick_status,
    null::smallint                                                         as pick_no,
    null::smallint                                                         as faab_amount,
    t.season                                                               as value_from_season,
    t.week                                                                 as value_from_week,
    t.created_at                                                           as value_from_at
from trades t
join core.transaction_player a on a.transaction_id = t.transaction_id and a.action = 'add'
left join core.transaction_player d on d.transaction_id = t.transaction_id and d.action = 'drop'
                                   and d.player_id = a.player_id
union all
select
    t.transaction_id || ':pick:' || tp.pick_season || '-' || tp.pick_round || '-' || tp.original_code,
    t.transaction_id, t.season, t.week, t.created_at,
    'pick',
    tp.to_code,
    tp.from_code,
    case when ps.pick_status in ('drafted', 'keeper') then x.player_id end,
    tp.pick_season, tp.pick_round, tp.original_code,
    ps.pick_status,
    case when ps.pick_status in ('drafted', 'keeper') then x.pick_no end,
    null,
    case when ps.pick_status = 'drafted' then tp.pick_season end,
    case when ps.pick_status = 'drafted' then 1::smallint end,
    case when ps.pick_status = 'drafted' then coalesce(x.draft_started_at, '-infinity'::timestamptz) end
from trades t
join core.transaction_pick tp on tp.transaction_id = t.transaction_id
left join core.v_traded_pick x on x.pick_season = tp.pick_season and x.pick_round = tp.pick_round
                              and x.original_code = tp.original_code
cross join lateral (
    select case when x.final_transaction_id is distinct from t.transaction_id then 'traded_on'
                else x.status end as pick_status
) ps
union all
select
    t.transaction_id || ':faab:' || f.from_code || '-' || f.to_code,
    t.transaction_id, t.season, t.week, t.created_at,
    'faab', f.to_code, f.from_code, null, null, null, null, null, null, f.amount, null, null, null
from trades t
join core.transaction_faab f on f.transaction_id = t.transaction_id;

comment on view core.v_trade_asset is
    'Everything each team received in every completed trade: players, picks (with the player drafted) and FAAB.';


-- ---------------------------------------------------------------------
-- Stints: each valued asset's player on the receiving team, one row per season.
-- The first row is the trade season (or the draft season for a pick). A next
-- season is added while that team kept him (core.keeper or a keeper pick) and
-- hadn't dropped or traded him away in the season before.
-- end_week = the week that team added him again (a new acquisition starts
-- there; exclusive). left_roster = he was dropped / traded away that season.
-- ---------------------------------------------------------------------
create or replace view core.v_trade_stint as
with recursive
kept as (
    select season, franchise_code, player_id from core.keeper
    union
    select season, franchise_code, player_id from core.draft_pick where is_keeper and player_id is not null
),
drops as (
    select t.season, t.created_at, tp.franchise_code, tp.player_id
    from core.transaction t
    join core.transaction_player tp on tp.transaction_id = t.transaction_id
    where t.status = 'complete' and tp.action = 'drop'
),
stint as (
    select a.asset_id, a.transaction_id, a.receiver_code, a.player_id,
           a.value_from_season as season, a.value_from_week as start_week, a.value_from_at as start_at,
           0 as kept_seasons
    from core.v_trade_asset a
    where a.value_from_season is not null and a.player_id is not null
    union all
    select s.asset_id, s.transaction_id, s.receiver_code, s.player_id,
           (s.season + 1)::smallint, 1::smallint, '-infinity'::timestamptz, s.kept_seasons + 1
    from stint s
    where exists (select 1 from kept k
                  where k.season = s.season + 1 and k.franchise_code = s.receiver_code and k.player_id = s.player_id)
      and not exists (select 1 from drops d
                      where d.season = s.season and d.franchise_code = s.receiver_code
                        and d.player_id = s.player_id and d.created_at > s.start_at)
)
select
    s.asset_id, s.transaction_id, s.receiver_code, s.player_id, s.season, s.start_week, s.start_at, s.kept_seasons,
    (select min(t.week)
     from core.transaction t
     join core.transaction_player tp on tp.transaction_id = t.transaction_id
     where t.status = 'complete' and t.type in ('trade', 'waiver', 'free_agent') and tp.action = 'add'
       and t.season = s.season and tp.franchise_code = s.receiver_code and tp.player_id = s.player_id
       and (t.week, t.created_at) > (s.start_week, s.start_at))                       as end_week,
    exists (select 1 from drops d
            where d.season = s.season and d.franchise_code = s.receiver_code
              and d.player_id = s.player_id and d.created_at > s.start_at)            as left_roster
from stint s;

comment on view core.v_trade_stint is
    'Each traded player (or player drafted with a traded pick) on the receiving team, season by season, until he left.';


-- ---------------------------------------------------------------------
-- Every player-week that counts for an asset. game_kind: regular, playoff (the
-- team played an official playoff game that week) or null (consolation game or
-- eliminated: listed, but never counted). points_over_replacement only for
-- weeks he started (null on the bench).
-- ---------------------------------------------------------------------
-- (MATERIALIZED: each piece is worked out once. Without it the planner re-ran the
-- replacement levels for every player-week: 14 s instead of well under 1 s.)
create or replace view core.v_trade_asset_week as
with playoff_weeks as materialized (
    select distinct season, week, franchise_code
    from core.v_team_game
    where game_type = 'playoff'
),
replacement as materialized (
    select season, position, per_week from core.v_replacement_level
),
stints as materialized (
    select * from core.v_trade_stint
)
select
    st.asset_id, st.transaction_id, st.receiver_code, st.player_id, st.kept_seasons,
    pw.season, pw.week,
    case when pw.week < s.playoff_week_start then 'regular'
         when pf.franchise_code is not null  then 'playoff' end            as game_kind,
    pw.is_starter, pw.lineup_slot, pw.points,
    rl.position                                                             as replacement_position,
    rl.per_week                                                             as replacement_per_week,
    case when pw.is_starter then pw.points - rl.per_week end               as points_over_replacement
from stints st
join core.player_week pw on pw.season = st.season and pw.franchise_code = st.receiver_code
                        and pw.player_id = st.player_id and pw.week >= st.start_week
                        and (st.end_week is null or pw.week < st.end_week)
join core.season s on s.season = pw.season
left join core.player p on p.player_id = pw.player_id
left join replacement rl on rl.season = pw.season
                         and rl.position = case p.position when 'FB' then 'RB' else p.position end
left join playoff_weeks pf on pf.season = pw.season and pf.week = pw.week and pf.franchise_code = pw.franchise_code;

comment on view core.v_trade_asset_week is
    'Every week a traded player (or a player drafted with a traded pick) was on the receiving team, '
    'with points over replacement when started.';


-- ---------------------------------------------------------------------
-- Totals per asset. value = points over replacement in counted starts
-- (regular season + playoff games). still_counting = the story isn't over:
-- a pick not drafted yet, or a player still on that roster in the newest season.
-- ---------------------------------------------------------------------
create or replace view core.v_trade_asset_value as
with weeks as materialized (
    select asset_id,
           count(*)                                                                as weeks_rostered,
           count(*) filter (where is_starter and game_kind is not null)            as weeks_started,
           sum(points) filter (where is_starter and game_kind is not null)         as points_started,
           sum(points_over_replacement) filter (where game_kind = 'regular')       as value_regular,
           sum(points_over_replacement) filter (where game_kind = 'playoff')       as value_playoff,
           count(*) filter (where is_starter and game_kind is not null
                              and replacement_per_week is null)                    as starts_without_replacement,
           count(distinct season)                                                  as seasons,
           max(season)                                                             as last_season
    from core.v_trade_asset_week
    group by asset_id
),
last_stint as materialized (
    select distinct on (asset_id) asset_id, season, kept_seasons, left_roster, end_week
    from core.v_trade_stint
    order by asset_id, season desc
)
select
    a.asset_id, a.transaction_id, a.season, a.week, a.created_at, a.asset_type,
    a.receiver_code, a.sender_code, a.player_id,
    a.pick_season, a.pick_round, a.pick_original_code, a.pick_status, a.pick_no, a.faab_amount,
    coalesce(w.weeks_rostered, 0)                                                  as weeks_rostered,
    coalesce(w.weeks_started, 0)                                                   as weeks_started,
    round(coalesce(w.points_started, 0), 2)                                        as points_started,
    round(coalesce(w.value_regular, 0), 2)                                         as value_regular,
    round(coalesce(w.value_playoff, 0), 2)                                         as value_playoff,
    round(coalesce(w.value_regular, 0) + coalesce(w.value_playoff, 0), 2)          as value,
    coalesce(w.starts_without_replacement, 0)                                      as starts_without_replacement,
    coalesce(w.seasons, 0)                                                         as seasons,
    coalesce(ls.kept_seasons, 0)                                                   as kept_seasons,
    w.last_season,
    case
        when a.asset_type = 'pick' and a.pick_status = 'not_drafted_yet'          then true
        when ls.season = (select max(season) from core.season)
             and not ls.left_roster and ls.end_week is null                        then true
        else false
    end                                                                            as still_counting
from core.v_trade_asset a
left join weeks w       on w.asset_id = a.asset_id
left join last_stint ls on ls.asset_id = a.asset_id;

comment on view core.v_trade_asset_value is
    'Per trade asset: weeks, starts, points, value (points over replacement started; regular season + playoffs).';


-- ---------------------------------------------------------------------
-- Per trade and team (every team that received or sent something).
-- before / after = that team's official regular-season games that season,
-- before the trade week and from the trade week on.
-- ---------------------------------------------------------------------
create or replace view core.v_trade_side as
with trades as (
    select transaction_id, season, week, created_at
    from core.transaction
    where type = 'trade' and status = 'complete'
),
vals as materialized (       -- worked out once, used three times below
    select * from core.v_trade_asset_value
),
sides as (
    select transaction_id, receiver_code as franchise_code from vals
    union
    select transaction_id, sender_code from vals where sender_code is not null
),
got as (
    select transaction_id, receiver_code as franchise_code,
           sum(value) as value, sum(value_regular) as value_regular, sum(value_playoff) as value_playoff,
           sum(points_started) as points_started, sum(weeks_started) as weeks_started,
           count(*) filter (where asset_type = 'player') as players,
           count(*) filter (where asset_type = 'pick')   as picks,
           coalesce(sum(faab_amount), 0)                 as faab,
           bool_or(still_counting)                       as still_counting
    from vals
    group by transaction_id, receiver_code
),
gave as (
    select transaction_id, sender_code as franchise_code,
           sum(value) as value, coalesce(sum(faab_amount), 0) as faab, bool_or(still_counting) as still_counting
    from vals
    where sender_code is not null
    group by transaction_id, sender_code
),
form as (
    select tr.transaction_id, g.franchise_code,
           count(*)          filter (where g.week < tr.week)                       as games_before,
           avg(g.points_for) filter (where g.week < tr.week)                       as ppg_before,
           count(*)          filter (where g.week < tr.week and g.result = 'W')    as wins_before,
           count(*)          filter (where g.week < tr.week and g.result = 'L')    as losses_before,
           count(*)          filter (where g.week < tr.week and g.result = 'T')    as ties_before,
           count(*)          filter (where g.week >= tr.week)                      as games_after,
           avg(g.points_for) filter (where g.week >= tr.week)                      as ppg_after,
           count(*)          filter (where g.week >= tr.week and g.result = 'W')   as wins_after,
           count(*)          filter (where g.week >= tr.week and g.result = 'L')   as losses_after,
           count(*)          filter (where g.week >= tr.week and g.result = 'T')   as ties_after
    from trades tr
    join sides sd on sd.transaction_id = tr.transaction_id
    join core.v_team_game g on g.season = tr.season and g.franchise_code = sd.franchise_code
                           and g.game_type = 'regular'
    group by tr.transaction_id, g.franchise_code
)
select
    tr.transaction_id, tr.season, tr.week, tr.created_at, sd.franchise_code,
    round(coalesce(got.value, 0), 2)                                                as value_received,
    round(coalesce(got.value_regular, 0), 2)                                        as value_received_regular,
    round(coalesce(got.value_playoff, 0), 2)                                        as value_received_playoff,
    round(coalesce(gave.value, 0), 2)                                               as value_given,
    round(coalesce(got.value, 0) - coalesce(gave.value, 0), 2)                      as net,
    case when coalesce(got.value, 0) - coalesce(gave.value, 0) >= 1  then 'won'
         when coalesce(got.value, 0) - coalesce(gave.value, 0) <= -1 then 'lost'
         else 'even' end                                                            as result,
    round(coalesce(got.points_started, 0), 2)                                       as points_started_received,
    coalesce(got.weeks_started, 0)                                                  as weeks_started_received,
    coalesce(got.players, 0)                                                        as players_received,
    coalesce(got.picks, 0)                                                          as picks_received,
    coalesce(got.faab, 0)                                                           as faab_received,
    coalesce(gave.faab, 0)                                                          as faab_sent,
    coalesce(got.still_counting, false) or coalesce(gave.still_counting, false)     as still_counting,
    coalesce(f.games_before, 0)                                                     as games_before,
    round(f.ppg_before, 2)                                                          as ppg_before,
    coalesce(f.wins_before, 0)   as wins_before,   coalesce(f.losses_before, 0) as losses_before,
    coalesce(f.ties_before, 0)   as ties_before,
    coalesce(f.games_after, 0)                                                      as games_after,
    round(f.ppg_after, 2)                                                           as ppg_after,
    coalesce(f.wins_after, 0)    as wins_after,    coalesce(f.losses_after, 0)  as losses_after,
    coalesce(f.ties_after, 0)    as ties_after,
    st.regular_season_rank, st.made_playoffs, st.final_place, st.champion
from trades tr
join sides sd             on sd.transaction_id = tr.transaction_id
left join got             on got.transaction_id = tr.transaction_id and got.franchise_code = sd.franchise_code
left join gave            on gave.transaction_id = tr.transaction_id and gave.franchise_code = sd.franchise_code
left join form f          on f.transaction_id = tr.transaction_id and f.franchise_code = sd.franchise_code
left join core.v_season_standings st on st.season = tr.season and st.franchise_code = sd.franchise_code;

comment on view core.v_trade_side is
    'Per trade and team: value received and given (points over replacement started), net, won/lost/even, '
    'FAAB, points per game before/after the trade that season, and how the season ended.';


-- ---------------------------------------------------------------------
-- Per trade: the teams, the winner (highest net, at least 1 point) and margin
-- (the winner's net; in a two-team trade = the gap in value received).
-- ---------------------------------------------------------------------
create or replace view core.v_trade as
with ranked as (
    select s.*, row_number() over (partition by s.transaction_id order by s.net desc, s.franchise_code) as rk
    from core.v_trade_side s
)
select
    transaction_id, season, week, created_at,
    array_agg(franchise_code order by franchise_code)                     as team_codes,
    count(*)                                                               as teams,
    case when max(net) filter (where rk = 1) >= 1
         then max(franchise_code) filter (where rk = 1) end                as winner_code,
    max(net) filter (where rk = 1)                                         as margin,
    sum(value_received)                                                    as total_value,
    bool_or(still_counting)                                                as still_counting
from ranked
group by transaction_id, season, week, created_at;

comment on view core.v_trade is
    'Per completed trade: teams, winner (highest net value, 1+ point) and margin. Details in core.v_trade_side.';
