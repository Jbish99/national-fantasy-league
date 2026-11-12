-- =====================================================================
-- db/seed/001_reference_data.sql
-- Hand-maintained reference data: franchises, season list, keeper rules,
-- and which Sleeper roster_id belongs to which franchise each season.
--
-- Safe to re-run: upserts on natural keys. Edit this file and re-run
-- whenever the brand registry or keeper rules change.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Franchises (from the brand registry, Appendix A)
-- MIS is a code made up for this database. The Muskrats aren't in the registry.
-- ---------------------------------------------------------------------
insert into core.franchise (franchise_code, location, nickname, home_city, founded_season, final_season, notes) values
    ('STO', 'Stonebrook',    'Stompers',     'Davison',       2020, null, null),
    ('DET', 'Detroit',       'Flood',        'Detroit',       2020, null, null),
    ('SDP', 'Sand Point',    'Sharks',       'Caseville',     2020, null, null),
    ('FAR', 'Farmington',    'Huskies',      'Farmington',    2020, null, 'Played as Houghton Huskies in 2021-22.'),
    ('VHC', 'Vehicle City',  'Victinis',     'Flint',         2020, null, null),
    ('GRB', 'Grand Blanc',   'Coins',        'Grand Blanc',   2020, null, null),
    ('OAP', 'Oak Park',      'Trash Pandas', 'Oak Park',      2020, null, 'Oak Park Prowlers is an alternate brand.'),
    ('OAK', 'Oakland',       'Panthers',     'Pontiac',       2020, null, null),
    ('UPN', 'Up North',      'Nightmare',    'Davison',       2021, null, 'Played as Ann Arbor Archers (2021-22) and Nurtingen Nightmare (2023).'),
    ('FLT', 'Flint',         'Kraken',       'Flint',         2021, null, 'Played as San Diego Lions in 2021.'),
    ('GRL', 'Great Lakes',   'Grizzlies',    'Traverse City', 2021, null, 'Played as San Antonio Raiders in 2021.'),
    ('GRR', 'Grand Rapids',  'Gryphons',     'Grand Rapids',  2022, null, 'Expansion franchise. Took over Sleeper roster slot 9 in 2022.'),
    ('MIS', 'Mississippi',   'Muskrats',     null,            2021, 2021, 'Defunct after 2021. Code assigned for this database, not in the brand registry.')
on conflict (franchise_code) do update set
    location       = excluded.location,
    nickname       = excluded.nickname,
    home_city      = excluded.home_city,
    founded_season = excluded.founded_season,
    final_season   = excluded.final_season,
    notes          = excluded.notes;


-- ---------------------------------------------------------------------
-- Seasons. The Sleeper loader fills in everything else (status,
-- settings, playoff weeks...). DO NOTHING so we never undo the loader.
-- ---------------------------------------------------------------------
insert into core.season (season, platform, sleeper_league_id, num_teams) values
    (2020, 'espn',    null,                  null),  -- team count comes with the ESPN sheet
    (2021, 'sleeper', '679456169764401152',  12),
    (2022, 'sleeper', '784412563000696832',  12),
    (2023, 'sleeper', '916897227614932992',  12),
    (2024, 'sleeper', '1067164089564139520', 12),
    (2025, 'sleeper', '1181386796347965440', 12),
    (2026, 'sleeper', '1334317252626882560', 12)
on conflict (season) do nothing;


-- ---------------------------------------------------------------------
-- Keeper rules (the season the players were kept FOR)
-- 2021-22: 3 restricted keepers. No 1st/2nd-round players, and you give
--          up the pick the player was drafted with the year before.
-- 2023-26: 2 Captains (unrestricted) + 2 restricted keepers.
--          ASSUMPTION: restricted keepers follow the 2021-22 rules. Confirm.
-- 2027:    3 keepers, no restrictions. Add a row once the 2027 season exists.
-- ---------------------------------------------------------------------
insert into core.keeper_rule
    (season, total_keepers, unrestricted_keepers, restricted_keepers, restricted_banned_rounds, restricted_costs_pick, notes)
values
    (2021, 3, 0, 3, '{1,2}', true, '3 restricted keepers.'),
    (2022, 3, 0, 3, '{1,2}', true, '3 restricted keepers.'),
    (2023, 4, 2, 2, '{1,2}', true, '2 Captains + 2 restricted keepers.'),
    (2024, 4, 2, 2, '{1,2}', true, '2 Captains + 2 restricted keepers.'),
    (2025, 4, 2, 2, '{1,2}', true, '2 Captains + 2 restricted keepers.'),
    (2026, 4, 2, 2, '{1,2}', true, '2 Captains + 2 restricted keepers.')
on conflict (season) do update set
    total_keepers            = excluded.total_keepers,
    unrestricted_keepers     = excluded.unrestricted_keepers,
    restricted_keepers       = excluded.restricted_keepers,
    restricted_banned_rounds = excluded.restricted_banned_rounds,
    restricted_costs_pick    = excluded.restricted_costs_pick,
    notes                    = excluded.notes;


-- ---------------------------------------------------------------------
-- Sleeper roster_id -> franchise, every Sleeper season.
-- Roster slots stayed with the same franchise every year, except slot 9:
-- Mississippi Muskrats in 2021, Grand Rapids Gryphons from 2022.
-- The loader fills owner, team name and division on these rows.
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
