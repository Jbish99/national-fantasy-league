-- =====================================================================
-- db/checks/002_phase2_field_checks.sql
-- One-time checks to run AFTER `python -m etl.pull_sleeper`.
-- They look at the raw Sleeper data to confirm a few things we
-- shouldn't guess about before writing the clean-layer loader.
-- Run the whole file in DBeaver (Alt+X). Each query opens its own result tab.
-- =====================================================================


-- 1. What got pulled: one row per season and endpoint.
--    Expect matchups = 17 and transactions = 18 for 2021-2025,
--    and matchups = the last completed week for 2026.
select season, endpoint, count(*) as responses
from raw.v_sleeper_latest
group by season, endpoint
order by season nulls first, endpoint;


-- 2. Empty lineup slots: how does Sleeper show a starting slot nobody filled?
--    Lists any team-week whose starters list has a '0' or blank entry, or is
--    a different length than that season's starting slots.
--    Zero rows = no one ever left a slot empty (also a useful answer).
with slots as (
    select l.season, count(*) filter (where pos <> 'BN') as starting_slots
    from raw.v_sleeper_latest l
    cross join jsonb_array_elements_text(l.payload -> 'roster_positions') as pos
    where l.endpoint = 'league'
    group by l.season
)
select r.season, r.week, m ->> 'roster_id' as roster_id, sl.starting_slots, m -> 'starters' as starters
from raw.v_sleeper_latest r
join slots sl on sl.season = r.season
cross join jsonb_array_elements(r.payload) as m
where r.endpoint = 'matchups'
  and (   jsonb_array_length(m -> 'starters') <> sl.starting_slots
       or exists (select 1
                  from jsonb_array_elements_text(m -> 'starters') as s
                  where s in ('0', '')))
order by r.season, r.week;


-- 3. FAAB in trades: what does a non-empty waiver_budget look like?
--    Zero rows = nobody has ever traded FAAB.
select r.season, r.week, t ->> 'transaction_id' as transaction_id, t -> 'waiver_budget' as waiver_budget
from raw.v_sleeper_latest r
cross join jsonb_array_elements(r.payload) as t
where r.endpoint = 'transactions'
  and case when jsonb_typeof(t -> 'waiver_budget') = 'array'
           then jsonb_array_length(t -> 'waiver_budget') > 0
           else false end
order by r.season, r.week
limit 10;
