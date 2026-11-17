-- =====================================================================
-- db/seed/001_reference_data.sql
-- Hand-maintained reference data: franchises, seasons (and whose scores
-- are official each year), keeper rules, the 2020 teams, which Sleeper
-- roster_id belongs to which franchise, official-result overrides, the
-- team identity labels and the formations (Phases 10 / 10b; rename them in the
-- last two sections).
--
-- Safe to re-run: upserts on natural keys. Edit this file and re-run
-- whenever the brand registry, rules or overrides change.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Franchises. Codes match the workbook's Game IDs.
-- ---------------------------------------------------------------------
insert into core.franchise (franchise_code, location, nickname, home_city, founded_season, final_season, notes) values
    ('STO', 'Stonebrook',    'Stompers',     'Davison',       2020, null, null),
    ('DET', 'Detroit',       'Flood',        'Detroit',       2020, null, null),
    ('SDP', 'Sand Point',    'Sharks',       'Caseville',     2020, null, null),
    ('FAR', 'Farmington',    'Huskies',      'Farmington',    2020, null, 'Played as Houghton Huskies in 2021-22 on Sleeper.'),
    ('VHC', 'Vehicle City',  'Victinis',     'Flint',         2020, null, null),
    ('GRB', 'Grand Blanc',   'Coins',        'Grand Blanc',   2020, null, null),
    ('OAP', 'Oak Park',      'Trash Pandas', 'Oak Park',      2020, null, 'Oak Park Prowlers is an alternate brand.'),
    ('OAK', 'Oakland',       'Panthers',     'Pontiac',       2020, null, null),
    ('MON', 'Montana',       'Oilers',       null,            2020, 2020, '2020 only (ESPN era). Separate from every later franchise.'),
    ('UPB', 'Up North',      'Blizzard',     null,            2020, 2020, '2020 only (ESPN era). Separate from Up North Nightmare (UPN).'),
    ('UPN', 'Up North',      'Nightmare',    'Davison',       2021, null, 'Played as Ann Arbor Archers (2021-22) and Nurtingen Nightmare (2023) on Sleeper.'),
    ('FLT', 'Flint',         'Kraken',       'Flint',         2021, null, 'Played as San Diego Lions in 2021 on Sleeper.'),
    ('GRL', 'Great Lakes',   'Grizzlies',    'Traverse City', 2021, null, 'Played as San Antonio Raiders in 2021 on Sleeper.'),
    ('MIS', 'Mississippi',   'Muskrats',     null,            2021, 2021, 'Defunct after 2021.'),
    ('GRR', 'Grand Rapids',  'Gryphons',     'Grand Rapids',  2022, null, 'Expansion franchise. Took over Sleeper roster slot 9 in 2022.')
on conflict (franchise_code) do update set
    location       = excluded.location,
    nickname       = excluded.nickname,
    home_city      = excluded.home_city,
    founded_season = excluded.founded_season,
    final_season   = excluded.final_season,
    notes          = excluded.notes;


-- ---------------------------------------------------------------------
-- Seasons and whose scores are official each year.
--   sheet   = the All-In-One workbook's scores (2020 ESPN; 2023 by league decision)
--   sleeper = Sleeper's scores (includes stat corrections), unless the
--             workbook has a different winner
-- The Sleeper loader fills everything else (status, settings, playoff weeks...).
-- ---------------------------------------------------------------------
insert into core.season (season, platform, official_scores_from, sleeper_league_id, num_teams) values
    (2020, 'espn',    'sheet',   null,                  10),
    (2021, 'sleeper', 'sleeper', '679456169764401152',  12),
    (2022, 'sleeper', 'sleeper', '784412563000696832',  12),
    (2023, 'sleeper', 'sheet',   '916897227614932992',  12),
    (2024, 'sleeper', 'sleeper', '1067164089564139520', 12),
    (2025, 'sleeper', 'sleeper', '1181386796347965440', 12),
    (2026, 'sleeper', 'sleeper', '1334317252626882560', 12)
on conflict (season) do update set
    platform             = excluded.platform,
    official_scores_from = excluded.official_scores_from,
    sleeper_league_id    = excluded.sleeper_league_id,
    num_teams            = excluded.num_teams;


-- ---------------------------------------------------------------------
-- Keeper rules (the season the players were kept FOR).
-- Captain vs keeper isn't tracked.
-- ---------------------------------------------------------------------
insert into core.keeper_rule (season, total_keepers, notes) values
    (2021, 3, 'No 1st- or 2nd-round players. Cost: the pick the player was drafted with the year before.'),
    (2022, 3, 'No 1st- or 2nd-round players. Cost: the pick the player was drafted with the year before.'),
    (2023, 4, '2 Captains + 2 keepers.'),
    (2024, 4, '2 Captains + 2 keepers.'),
    (2025, 4, '2 Captains + 2 keepers.'),
    (2026, 4, '2 Captains + 2 keepers.')
on conflict (season) do update set
    total_keepers = excluded.total_keepers,
    notes         = excluded.notes;


-- ---------------------------------------------------------------------
-- 2020 (ESPN): the ten teams and their names that year.
-- ---------------------------------------------------------------------
insert into core.franchise_season (season, franchise_code, team_name) values
    (2020, 'DET', 'Detroit Flood'),
    (2020, 'FAR', 'Farmington Huskies'),
    (2020, 'GRB', 'Grand Blanc Coins'),
    (2020, 'MON', 'Montana Oilers'),
    (2020, 'OAK', 'Oakland Panthers'),
    (2020, 'OAP', 'Oak Park Trash Pandas'),
    (2020, 'SDP', 'Sand Point Sharks'),
    (2020, 'STO', 'Stonebrook Stompers'),
    (2020, 'UPB', 'Up North Blizzard'),
    (2020, 'VHC', 'Vehicle City Victinis')
on conflict (season, franchise_code) do update set
    team_name = excluded.team_name;


-- ---------------------------------------------------------------------
-- 2021+: Sleeper roster_id -> franchise.
-- Roster slots stayed with the same franchise every year, except slot 9:
-- Mississippi Muskrats in 2021, Grand Rapids Gryphons from 2022.
-- The Sleeper loader fills owner, team name and division on these rows.
-- ---------------------------------------------------------------------
insert into core.franchise_season (season, franchise_code, sleeper_roster_id)
select
    s.season,
    case when s.season = 2021 and m.roster_id = 9 then 'MIS' else m.franchise_code end,
    m.roster_id
from generate_series(2021, 2026) as s(season)
cross join (values
    (1,  'DET'),
    (2,  'GRB'),
    (3,  'FAR'),
    (4,  'VHC'),
    (5,  'OAK'),
    (6,  'UPN'),
    (7,  'FLT'),
    (8,  'OAP'),
    (9,  'GRR'),
    (10, 'STO'),
    (11, 'SDP'),
    (12, 'GRL')
) as m(roster_id, franchise_code)
on conflict (season, franchise_code) do update set
    sleeper_roster_id = excluded.sleeper_roster_id;


-- ---------------------------------------------------------------------
-- Official-result overrides, applied on top of the workbook by the loader.
--   winner_code null  = no winner (tie / co-champions)
--   scores_known false = the workbook's scores are placeholders; keep the winner only
-- ---------------------------------------------------------------------
insert into core.game_override (game_id, winner_code, scores_known, reason) values
    ('2022-17-GRL-STO', null,  true,
        'Co-champions: the Bills-Bengals game (Damar Hamlin) was cancelled, so the Fantasy Bowl could not be finished.'),
    ('2020-14-GRB-UPB', 'GRB', false, '2020 consolation: workbook has placeholder scores, winner only.'),
    ('2020-14-MON-OAK', 'OAK', false, '2020 consolation: workbook has placeholder scores, winner only.'),
    ('2020-15-GRB-OAK', 'OAK', false, '2020 consolation: workbook has placeholder scores, winner only.'),
    ('2020-15-MON-UPB', 'UPB', false, '2020 consolation: workbook has placeholder scores, winner only.'),
    ('2020-16-OAK-UPB', 'OAK', false, '2020 consolation: workbook has placeholder scores, winner only.'),
    ('2020-16-FAR-SDP', 'SDP', false, '2020 consolation: workbook has placeholder scores, winner only.'),
    ('2020-16-DET-STO', 'DET', false, '2020 consolation: workbook has placeholder scores, winner only.'),
    ('2020-16-GRB-MON', 'MON', false, '2020 consolation: workbook has placeholder scores, winner only.')
on conflict (game_id) do update set
    winner_code  = excluded.winner_code,
    scores_known = excluded.scores_known,
    reason       = excluded.reason;


-- ---------------------------------------------------------------------
-- Team identities (Phase 10, Jaden 2026-11-16; football names + voice Phase 10b).
-- One scheme label (kind 'style': how a team scored) + one front-office label
-- (kind 'build': how its roster was put together) per team-season.
-- RENAME A LABEL HERE (name / description / evidence), then run
-- `python -m etl.run_sql --all` (the daily refresh does it too).
--   metric     the measure behind it (core.v_team_metric)
--   direction  1 = a high value earns it, -1 = a low value
--   min_z      how many standard deviations from that season's league average
--              a team must be (1.0 = "clearly stands out"); the all-time label uses
--              the average over a franchise's seasons, with its own bar (min_alltime_z,
--              0.5 by default, set in db/schema/016)
--   min_value  optional: the value must also reach this (Wheeler-Dealer / Waiver Wire
--              Warrior: at least 10% of the starters' points from that source)
--   evidence   the fact sentence (hover a label); {value} and {avg} are filled in
--              using value_format (pct, pct_inverse = 1 - value, years, points)
-- The codes (air_raid, steady_eddie, ...) never change: the scouting reports in
-- app/story.py are keyed by them. Renamed 2026-11-16 (Phase 10b): steady_eddie ->
-- Ball Control, boom_or_bust -> Big-Play Offense, draft_purist -> Draft and Develop,
-- keeper_core -> Franchise Tag, trade_shark -> Wheeler-Dealer.
-- The row of each kind with no metric is the fallback when nothing stands out.
-- To remove a label, delete its row in the database too (an upsert never deletes).
-- ---------------------------------------------------------------------
insert into core.identity (identity_code, name, kind, metric, direction, min_z, min_value, description, evidence, value_format, sort_order) values
    ('air_raid',            'Air-Raid',            'style', 'ground_share',  -1, 1.0, null,
        'Spread it out and let it fly: more of its points came through the air (passing and catching) than the rest of the league.',
        '{value} of its offense through the air (league {avg})', 'pct_inverse', 1),
    ('ground_and_pound',    'Ground and Pound',    'style', 'ground_share',   1, 1.0, null,
        'Three yards and a cloud of dust: more of its points came on the ground than the rest of the league.',
        '{value} of its offense on the ground (league {avg})', 'pct', 2),
    ('gunslinger',          'Gunslinger',          'style', 'qb_share',       1, 1.0, null,
        'The quarterback had the keys: its QB carried far more of the scoring than the league''s typical QB.',
        'QB scored {value} of its points (league {avg})', 'pct', 3),
    ('stars_and_scrubs',    'Stars & Scrubs',      'style', 'top2_share',     1, 1.0, null,
        'Two stars and a cast of role players: its top 2 starters scored a big share of the points.',
        'Top 2 starters scored {value} of its points (league {avg})', 'pct', 4),
    ('balanced_attack',     'Balanced Attack',     'style', 'top2_share',    -1, 1.0, null,
        'Everybody eats: points came from all over the lineup, not from a couple of stars.',
        'Top 2 starters scored just {value} of its points (league {avg})', 'pct', 5),
    ('steady_eddie',        'Ball Control',        'style', 'score_cv',      -1, 1.0, null,
        'Grind it out and win the week: some of the steadiest weekly scores in the league.',
        'Weekly scores swung ±{value} around its average (league ±{avg})', 'pct', 6),
    ('boom_or_bust',        'Big-Play Offense',    'style', 'score_cv',       1, 1.0, null,
        'Live by the big play: huge weeks and duds, some of the widest weekly swings in the league.',
        'Weekly scores swung ±{value} around its average (league ±{avg})', 'pct', 7),
    ('well_rounded',        'Well-Rounded',        'style', null,          null, 1.0, null,
        'No one trick on offense: nothing about how it scored stood out from the rest of the league.', null, null, 8),
    ('draft_purist',        'Draft and Develop',   'build', 'drafted_share',  1, 1.0, null,
        'Built through the draft: more of its points came from that year''s draft class than the rest of the league.',
        '{value} of its points from that year''s draft (league {avg})', 'pct', 11),
    ('keeper_core',         'Franchise Tag',       'build', 'kept_share',     1, 1.0, null,
        'Kept the core together: its keepers did the heavy lifting.',
        '{value} of its points from keepers (league {avg})', 'pct', 12),
    ('trade_shark',         'Wheeler-Dealer',      'build', 'trade_share',    1, 1.0, 0.10,
        'Always working the phones: built the lineup through trades.',
        '{value} of its points from players it traded for (league {avg})', 'pct', 13),
    ('waiver_wire_warrior', 'Waiver Wire Warrior', 'build', 'pickup_share',   1, 1.0, 0.10,
        'Found gold on the waiver wire: pickups and free agents carried the lineup.',
        '{value} of its points from pickups (league {avg})', 'pct', 14),
    ('youth_movement',      'Youth Movement',      'build', 'avg_age',       -1, 1.0, null,
        'Young legs everywhere: one of the youngest starting lineups in the league.',
        'Starters averaged {value} old (league {avg})', 'years', 15),
    ('old_guard',           'Old Guard',           'build', 'avg_age',        1, 1.0, null,
        'Win-now veterans: one of the oldest starting lineups in the league.',
        'Starters averaged {value} old (league {avg})', 'years', 16),
    ('deep_bench',          'Deep Bench',          'build', 'bench_ppw',      1, 1.0, null,
        'Depth for days: plenty of points sitting on the bench every week.',
        '{value} bench points a week (league {avg})', 'points', 17),
    ('mixed_build',         'Mixed Build',         'build', null,          null, 1.0, null,
        'No one blueprint: the draft, keepers, trades and pickups all chipped in.', null, null, 18)
on conflict (identity_code) do update set
    name         = excluded.name,
    kind         = excluded.kind,
    metric       = excluded.metric,
    direction    = excluded.direction,
    min_z        = excluded.min_z,
    min_value    = excluded.min_value,
    description  = excluded.description,
    evidence     = excluded.evidence,
    value_format = excluded.value_format,
    sort_order   = excluded.sort_order;


-- ---------------------------------------------------------------------
-- Formations (Phase 10b, Jaden 2026-11-16): named after what a team put in its
-- 2 FLEX spots each week (rule in db/schema/017). A team's base formation is the
-- one it lined up in for more than half its counted weeks, else the fallback.
-- RENAME HERE (name / flex_set / description); the codes are fixed by the rule
-- in core.v_team_week_formation, so keep them.
-- ---------------------------------------------------------------------
insert into core.formation (formation_code, name, flex_set, description, sort_order, is_fallback) values
    ('four_wide', 'Four-Wide Spread', 'WR + WR',
        'Spread the field: wide receivers in both FLEX spots, four wideouts in all.', 1, false),
    ('pro_set',   'Pro Set',          'RB + WR',
        'The classic balanced look: a running back and a wide receiver in the FLEX.', 2, false),
    ('power_i',   'Power I',          'RB + RB',
        'Line up and run it: running backs in both FLEX spots, four backs in all.', 3, false),
    ('heavy_set', 'Heavy Set',        'TE + RB or WR',
        'Bring in the big boys: a second tight end on the field in one FLEX spot.', 4, false),
    ('jumbo',     'Jumbo',            'TE + TE',
        'Goal-line package: tight ends in both FLEX spots, three in all.', 5, false),
    ('multiple',  'Multiple',         null,
        'No base set: never lined up the same way in more than half its weeks. Kept defenses guessing.', 6, true)
on conflict (formation_code) do update set
    name        = excluded.name,
    flex_set    = excluded.flex_set,
    description = excluded.description,
    sort_order  = excluded.sort_order,
    is_fallback = excluded.is_fallback;
