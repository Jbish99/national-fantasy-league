-- =====================================================================
-- db/schema/006_league_points.sql
-- Our league's fantasy points, rebuilt from each real NFL stat line with that
-- season's Sleeper scoring rules (core.scoring_setting).
--
-- Why: checks every Sleeper -> NFL id link (a wrong link shows up as points
-- that don't match Sleeper's), and later lets the app score players who
-- weren't on a roster (waiver pickups that would have helped, etc.).
--
-- Covers every offensive and kicking rule Sleeper offers that a weekly stat
-- line can answer. Rules that need play-by-play detail (e.g. points per
-- 40+ yard catch) aren't covered; qa.scoring_not_covered lists any of those
-- the league has turned on. Defense (DEF) scoring is not covered yet.
-- Tested 2026-11-13 against 15 real Sleeper weeks (Tyler Conklin, 2021-2024): all exact.
--
-- Run after 005. Safe to re-run.
-- =====================================================================

create or replace view core.v_scoring_rules as
select
    season,
        coalesce(max(points) filter (where stat_key = 'pass_yd'), 0)                as pass_yd,
        coalesce(max(points) filter (where stat_key = 'pass_td'), 0)                as pass_td,
        coalesce(max(points) filter (where stat_key = 'pass_int'), 0)               as pass_int,
        coalesce(max(points) filter (where stat_key = 'pass_2pt'), 0)               as pass_2pt,
        coalesce(max(points) filter (where stat_key = 'pass_fd'), 0)                as pass_fd,
        coalesce(max(points) filter (where stat_key = 'pass_att'), 0)               as pass_att,
        coalesce(max(points) filter (where stat_key = 'pass_cmp'), 0)               as pass_cmp,
        coalesce(max(points) filter (where stat_key = 'pass_inc'), 0)               as pass_inc,
        coalesce(max(points) filter (where stat_key = 'pass_sack'), 0)              as pass_sack,
        coalesce(max(points) filter (where stat_key = 'rush_yd'), 0)                as rush_yd,
        coalesce(max(points) filter (where stat_key = 'rush_td'), 0)                as rush_td,
        coalesce(max(points) filter (where stat_key = 'rush_2pt'), 0)               as rush_2pt,
        coalesce(max(points) filter (where stat_key = 'rush_fd'), 0)                as rush_fd,
        coalesce(max(points) filter (where stat_key = 'rush_att'), 0)               as rush_att,
        coalesce(max(points) filter (where stat_key = 'rec'), 0)                    as rec,
        coalesce(max(points) filter (where stat_key = 'rec_yd'), 0)                 as rec_yd,
        coalesce(max(points) filter (where stat_key = 'rec_td'), 0)                 as rec_td,
        coalesce(max(points) filter (where stat_key = 'rec_2pt'), 0)                as rec_2pt,
        coalesce(max(points) filter (where stat_key = 'rec_fd'), 0)                 as rec_fd,
        coalesce(max(points) filter (where stat_key = 'bonus_rec_te'), 0)           as bonus_rec_te,
        coalesce(max(points) filter (where stat_key = 'bonus_rec_rb'), 0)           as bonus_rec_rb,
        coalesce(max(points) filter (where stat_key = 'bonus_rec_wr'), 0)           as bonus_rec_wr,
        coalesce(max(points) filter (where stat_key = 'bonus_pass_yd_300'), 0)      as bonus_pass_yd_300,
        coalesce(max(points) filter (where stat_key = 'bonus_pass_yd_400'), 0)      as bonus_pass_yd_400,
        coalesce(max(points) filter (where stat_key = 'bonus_rush_yd_100'), 0)      as bonus_rush_yd_100,
        coalesce(max(points) filter (where stat_key = 'bonus_rush_yd_200'), 0)      as bonus_rush_yd_200,
        coalesce(max(points) filter (where stat_key = 'bonus_rec_yd_100'), 0)       as bonus_rec_yd_100,
        coalesce(max(points) filter (where stat_key = 'bonus_rec_yd_200'), 0)       as bonus_rec_yd_200,
        coalesce(max(points) filter (where stat_key = 'bonus_rush_rec_yd_100'), 0)  as bonus_rush_rec_yd_100,
        coalesce(max(points) filter (where stat_key = 'bonus_rush_rec_yd_200'), 0)  as bonus_rush_rec_yd_200,
        coalesce(max(points) filter (where stat_key = 'bonus_pass_cmp_25'), 0)      as bonus_pass_cmp_25,
        coalesce(max(points) filter (where stat_key = 'bonus_rush_att_20'), 0)      as bonus_rush_att_20,
        coalesce(max(points) filter (where stat_key = 'fum'), 0)                    as fum,
        coalesce(max(points) filter (where stat_key = 'fum_lost'), 0)               as fum_lost,
        coalesce(max(points) filter (where stat_key = 'st_td'), 0)                  as st_td,
        coalesce(max(points) filter (where stat_key = 'fum_rec_td'), 0)             as fum_rec_td,
        coalesce(max(points) filter (where stat_key = 'kr_yd'), 0)                  as kr_yd,
        coalesce(max(points) filter (where stat_key = 'pr_yd'), 0)                  as pr_yd,
        coalesce(max(points) filter (where stat_key = 'xpm'), 0)                    as xpm,
        coalesce(max(points) filter (where stat_key = 'xpmiss'), 0)                 as xpmiss,
        coalesce(max(points) filter (where stat_key = 'fgm'), 0)                    as fgm,
        coalesce(max(points) filter (where stat_key = 'fgmiss'), 0)                 as fgmiss,
        coalesce(max(points) filter (where stat_key = 'fgm_yds'), 0)                as fgm_yds,
        coalesce(max(points) filter (where stat_key = 'fgm_0_19'), 0)               as fgm_0_19,
        coalesce(max(points) filter (where stat_key = 'fgm_20_29'), 0)              as fgm_20_29,
        coalesce(max(points) filter (where stat_key = 'fgm_30_39'), 0)              as fgm_30_39,
        coalesce(max(points) filter (where stat_key = 'fgm_40_49'), 0)              as fgm_40_49,
        coalesce(max(points) filter (where stat_key = 'fgm_50_59'), 0)              as fgm_50_59,
        coalesce(max(points) filter (where stat_key = 'fgm_60p'), 0)                as fgm_60p,
        coalesce(max(points) filter (where stat_key = 'fgm_50p'), 0)                as fgm_50p,
        coalesce(max(points) filter (where stat_key = 'fgmiss_0_19'), 0)            as fgmiss_0_19,
        coalesce(max(points) filter (where stat_key = 'fgmiss_20_29'), 0)           as fgmiss_20_29,
        coalesce(max(points) filter (where stat_key = 'fgmiss_30_39'), 0)           as fgmiss_30_39,
        coalesce(max(points) filter (where stat_key = 'fgmiss_40_49'), 0)           as fgmiss_40_49,
        coalesce(max(points) filter (where stat_key = 'fgmiss_50_59'), 0)           as fgmiss_50_59,
        coalesce(max(points) filter (where stat_key = 'fgmiss_60p'), 0)             as fgmiss_60p,
        coalesce(max(points) filter (where stat_key = 'fgmiss_50p'), 0)             as fgmiss_50p
from core.scoring_setting
group by season;

comment on view core.v_scoring_rules is
    'One row per season: the points per unit for each Sleeper scoring rule that real stat lines can answer (0 = rule off).';


create or replace view core.v_nfl_league_points as
select
    n.gsis_id,
    n.season,
    n.week,
    n.season_type,
    n.player_name,
    n.position,
    n.team,
    round((
          0
        + coalesce(n.passing_yards, 0) * s.pass_yd
        + coalesce(n.passing_tds, 0) * s.pass_td
        + coalesce(n.passing_interceptions, 0) * s.pass_int
        + coalesce(n.passing_2pt_conversions, 0) * s.pass_2pt
        + coalesce(n.passing_first_downs, 0) * s.pass_fd
        + coalesce(n.attempts, 0) * s.pass_att
        + coalesce(n.completions, 0) * s.pass_cmp
        + (coalesce(n.attempts, 0) - coalesce(n.completions, 0)) * s.pass_inc
        + coalesce(n.sacks_suffered, 0) * s.pass_sack
        + coalesce(n.rushing_yards, 0) * s.rush_yd
        + coalesce(n.rushing_tds, 0) * s.rush_td
        + coalesce(n.rushing_2pt_conversions, 0) * s.rush_2pt
        + coalesce(n.rushing_first_downs, 0) * s.rush_fd
        + coalesce(n.carries, 0) * s.rush_att
        + coalesce(n.receptions, 0) * s.rec
        + coalesce(n.receiving_yards, 0) * s.rec_yd
        + coalesce(n.receiving_tds, 0) * s.rec_td
        + coalesce(n.receiving_2pt_conversions, 0) * s.rec_2pt
        + coalesce(n.receiving_first_downs, 0) * s.rec_fd
        + (case when n.position = 'TE' then coalesce(n.receptions, 0) else 0 end) * s.bonus_rec_te
        + (case when n.position = 'RB' then coalesce(n.receptions, 0) else 0 end) * s.bonus_rec_rb
        + (case when n.position = 'WR' then coalesce(n.receptions, 0) else 0 end) * s.bonus_rec_wr
        + (case when coalesce(n.passing_yards, 0) >= 300 then 1 else 0 end) * s.bonus_pass_yd_300
        + (case when coalesce(n.passing_yards, 0) >= 400 then 1 else 0 end) * s.bonus_pass_yd_400
        + (case when coalesce(n.rushing_yards, 0) >= 100 then 1 else 0 end) * s.bonus_rush_yd_100
        + (case when coalesce(n.rushing_yards, 0) >= 200 then 1 else 0 end) * s.bonus_rush_yd_200
        + (case when coalesce(n.receiving_yards, 0) >= 100 then 1 else 0 end) * s.bonus_rec_yd_100
        + (case when coalesce(n.receiving_yards, 0) >= 200 then 1 else 0 end) * s.bonus_rec_yd_200
        + (case when (coalesce(n.rushing_yards, 0) + coalesce(n.receiving_yards, 0)) >= 100 then 1 else 0 end) * s.bonus_rush_rec_yd_100
        + (case when (coalesce(n.rushing_yards, 0) + coalesce(n.receiving_yards, 0)) >= 200 then 1 else 0 end) * s.bonus_rush_rec_yd_200
        + (case when coalesce(n.completions, 0) >= 25 then 1 else 0 end) * s.bonus_pass_cmp_25
        + (case when coalesce(n.carries, 0) >= 20 then 1 else 0 end) * s.bonus_rush_att_20
        + (coalesce(n.sack_fumbles, 0) + coalesce(n.rushing_fumbles, 0) + coalesce(n.receiving_fumbles, 0)) * s.fum
        + (coalesce(n.sack_fumbles_lost, 0) + coalesce(n.rushing_fumbles_lost, 0) + coalesce(n.receiving_fumbles_lost, 0)) * s.fum_lost
        + coalesce(n.special_teams_tds, 0) * s.st_td
        + coalesce(n.fumble_recovery_tds, 0) * s.fum_rec_td
        + coalesce(n.kickoff_return_yards, 0) * s.kr_yd
        + coalesce(n.punt_return_yards, 0) * s.pr_yd
        + coalesce(n.pat_made, 0) * s.xpm
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
    )::numeric, 2) as league_points
from core.nfl_player_week n
join core.v_scoring_rules s on s.season = n.season;

comment on view core.v_nfl_league_points is
    'Every NFL stat line scored with our league''s rules for that season. Matches Sleeper''s player points when the id link is right.';


-- Sleeper scoring rules this view can answer (used by qa.scoring_not_covered)
create or replace view core.v_scoring_rules_covered as
select stat_key from (values
        ('pass_yd'), ('pass_td'), ('pass_int'), ('pass_2pt'), ('pass_fd'), ('pass_att'), ('pass_cmp'),
        ('pass_inc'), ('pass_sack'), ('rush_yd'), ('rush_td'), ('rush_2pt'), ('rush_fd'), ('rush_att'),
        ('rec'), ('rec_yd'), ('rec_td'), ('rec_2pt'), ('rec_fd'), ('bonus_rec_te'), ('bonus_rec_rb'),
        ('bonus_rec_wr'), ('bonus_pass_yd_300'), ('bonus_pass_yd_400'), ('bonus_rush_yd_100'),
        ('bonus_rush_yd_200'), ('bonus_rec_yd_100'), ('bonus_rec_yd_200'), ('bonus_rush_rec_yd_100'),
        ('bonus_rush_rec_yd_200'), ('bonus_pass_cmp_25'), ('bonus_rush_att_20'), ('fum'), ('fum_lost'),
        ('st_td'), ('fum_rec_td'), ('kr_yd'), ('pr_yd'), ('xpm'), ('xpmiss'), ('fgm'), ('fgmiss'),
        ('fgm_yds'), ('fgm_0_19'), ('fgm_20_29'), ('fgm_30_39'), ('fgm_40_49'), ('fgm_50_59'), ('fgm_60p'),
        ('fgm_50p'), ('fgmiss_0_19'), ('fgmiss_20_29'), ('fgmiss_30_39'), ('fgmiss_40_49'),
        ('fgmiss_50_59'), ('fgmiss_60p'), ('fgmiss_50p')
) v(stat_key);

comment on view core.v_scoring_rules_covered is 'Sleeper scoring keys that core.v_nfl_league_points knows how to score.';
