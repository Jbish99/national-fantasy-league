-- =====================================================================
-- db/seed/001_reference_data.sql
-- Hand-maintained reference data: franchises, seasons (and whose scores
-- are official each year), keeper rules, the 2020 teams, which Sleeper
-- roster_id belongs to which franchise, and official-result overrides.
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
