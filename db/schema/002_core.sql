-- =====================================================================
-- db/schema/002_core.sql
-- CORE layer: clean, normalized tables the dashboard reads from.
--
-- Big ideas:
--   * History belongs to the FRANCHISE (rulebook 3.3), keyed by the
--     3-letter code the league uses (DET, FLT, GRR...). Owners, Sleeper
--     roster_ids and team names hang off franchise_season.
--   * Two layers of game results:
--       - core.game          = the OFFICIAL league record (from the
--                              All-In-One workbook, plus Sleeper scores
--                              where the season policy says so)
--       - core.team_week /   = exactly what Sleeper reports, down to
--         core.player_week     every player's points and lineup slot
--     Standings, champions and the record book read core.game.
--     Lineup analysis reads team_week / player_week.
--
-- Natural primary keys everywhere so loaders can upsert safely.
-- Safe to re-run: everything uses IF NOT EXISTS.
-- To rebuild from scratch, run db/reset_core.sql first.
-- =====================================================================

create schema if not exists core;
comment on schema core is 'Clean, normalized league history. The Streamlit app reads from here.';


-- =====================================================================
-- 1. WHO: franchises, owners, seasons
-- =====================================================================

create table if not exists core.franchise (
    franchise_code  text     primary key check (franchise_code ~ '^[A-Z]{3}$'),
    location        text     not null,   -- 'Detroit', 'Stonebrook', 'Up North'
    nickname        text     not null,   -- 'Flood', 'Stompers', 'Nightmare'
    home_city       text,                -- registry City column (Davison, Caseville...)
    founded_season  smallint not null,
    final_season    smallint,            -- null = still active
    notes           text
);
comment on table core.franchise is
    'One row per franchise, keyed by the league''s 3-letter code. Owns all team history, records and trophies (rulebook 3.3).';


create table if not exists core.owner (
    owner_id         integer generated always as identity primary key,
    sleeper_user_id  text unique,        -- main identity for 2021+
    espn_member_id   text unique,        -- for the 2020 ESPN season, if we ever get it
    display_name     text,               -- latest Sleeper display name
    real_name        text,               -- fill in by hand
    nickname         text,               -- fill in by hand
    constraint ck_owner_identified
        check (sleeper_user_id is not null or espn_member_id is not null or real_name is not null)
);
comment on table core.owner is
    'People who have run a franchise. Tracked by Sleeper user_id across seasons.';


create table if not exists core.season (
    season                      smallint primary key,
    platform                    text     not null check (platform in ('espn', 'sleeper')),
    official_scores_from        text     not null default 'sleeper'
                                         check (official_scores_from in ('sheet', 'sleeper')),
    sleeper_league_id           text unique,
    sleeper_previous_league_id  text,
    sleeper_draft_id            text,
    status                      text,      -- Sleeper: pre_draft, drafting, in_season, complete
    num_teams                   smallint,
    playoff_week_start          smallint,  -- regular season = weeks before this
    playoff_teams               smallint,
    last_scored_week            smallint,  -- Sleeper last_scored_leg; useful for in-season refresh
    league_settings             jsonb,     -- full Sleeper settings object, for reference
    updated_at                  timestamptz not null default now()
);
comment on table core.season is
    'One row per league season (2020 ESPN, 2021+ Sleeper). official_scores_from says whose scores are official that year.';
comment on column core.season.official_scores_from is
    'sheet = workbook scores are official (2020, 2023). sleeper = Sleeper scores are official unless the workbook has a different winner.';


create table if not exists core.scoring_setting (
    season    smallint     not null references core.season,
    stat_key  text         not null,   -- Sleeper key: rec, pass_yd, rush_fd, fgm_yds...
    points    numeric(8,4) not null,   -- points per unit (0.04 per passing yard, etc.)
    primary key (season, stat_key)
);
comment on table core.scoring_setting is
    'Scoring rules per season, one row per stat. Lets us track rule changes and later recompute points from real stats.';


create table if not exists core.roster_slot (
    season      smallint not null references core.season,
    slot_index  smallint not null,     -- 0-based position in Sleeper roster_positions
    slot        text     not null,     -- QB, RB, WR, TE, FLEX, K, DEF, BN
    primary key (season, slot_index)
);
comment on table core.roster_slot is
    'Lineup slots per season in Sleeper order. starters[i] in a matchup fills slot_index i.';


create table if not exists core.keeper_rule (
    season         smallint primary key references core.season,
    total_keepers  smallint not null,
    notes          text
);
comment on table core.keeper_rule is 'How many players each team could keep, per season. Hand-maintained.';


create table if not exists core.franchise_season (
    season                   smallint not null references core.season,
    franchise_code           text     not null references core.franchise,
    owner_id                 integer  references core.owner,   -- filled by the loader
    sleeper_roster_id        smallint,                         -- only unique within a season
    team_name                text,                             -- name shown that season
    division_number          smallint,
    division_name            text,
    conference               text,
    -- Sleeper's own season totals from /rosters. Used ONLY to validate the Sleeper load.
    reported_wins            smallint,
    reported_losses          smallint,
    reported_ties            smallint,
    reported_points_for      numeric(8,2),
    reported_points_against  numeric(8,2),
    reported_max_points      numeric(8,2),   -- Sleeper ppts (best possible lineup)
    primary key (season, franchise_code),
    constraint uq_franchise_season_roster unique (season, sleeper_roster_id)
);
comment on table core.franchise_season is
    'Which owner, Sleeper roster_id, team name and division each franchise had in each season.';


-- =====================================================================
-- 2. PLAYERS
-- =====================================================================

create table if not exists core.player (
    player_id          text primary key,  -- Sleeper id; team defenses use the team abbreviation ('DAL')
    full_name          text,
    first_name         text,
    last_name          text,
    position           text,
    fantasy_positions  text[],
    nfl_team           text,              -- current team as of the latest /players/nfl pull
    status             text,
    birth_date         date,
    rookie_year        smallint,
    college            text,
    gsis_id            text,              -- nflverse key, filled in Phase 3
    espn_id            text,
    yahoo_id           text,
    sportradar_id      text,
    is_stub            boolean not null default false,  -- id showed up in league data but not in /players/nfl
    updated_at         timestamptz not null default now()
);
comment on table core.player is
    'Every player (and team defense) that appears anywhere in league history. Loaded from /players/nfl.';

create index if not exists ix_player_name on core.player (lower(full_name));
create index if not exists ix_player_gsis on core.player (gsis_id);


-- =====================================================================
-- 3. OFFICIAL GAME RECORD (from the All-In-One workbook)
-- =====================================================================

create table if not exists core.game (
    game_id                 text         primary key,  -- workbook Game ID, e.g. '2025-15-DET-OAK'
    season                  smallint     not null references core.season,
    week                    smallint     not null check (week between 1 and 18),
    game_type               text         not null check (game_type in ('regular', 'playoff', 'consolation')),
    designation             text,        -- workbook Special Designation: Fantasy Bowl, 3rd Place Bowl, 1st Round, IST Game...
    team1_code              text         not null,     -- alphabetical order, same as the Game ID
    team2_code              text         not null,
    team1_points            numeric(7,2),              -- official score; null when only the winner is known
    team2_points            numeric(7,2),
    winner_code             text,                      -- null = no winner (tie or co-champions)
    points_source           text         not null check (points_source in ('sheet', 'sleeper')),
    result_note             text,                      -- why the result is special (co-champions, placeholder scores...)
    international_location  text,                      -- country, for international games
    is_game_of_week         boolean      not null default false,
    is_division_game        boolean      not null default false,
    is_conference_game      boolean      not null default false,
    constraint ck_game_team_order check (team1_code < team2_code),
    constraint ck_game_winner check (winner_code is null or winner_code in (team1_code, team2_code)),
    constraint uq_game_teams unique (season, week, team1_code, team2_code),
    foreign key (season, team1_code) references core.franchise_season,
    foreign key (season, team2_code) references core.franchise_season
);
comment on table core.game is
    'The OFFICIAL league game record, one row per game, 2020 to now. Built from the All-In-One workbook (plus Sleeper scores where the season policy says so). Standings and champions read from here.';

create index if not exists ix_game_season_week on core.game (season, week);


create table if not exists core.game_override (
    game_id       text    primary key,     -- workbook Game ID
    winner_code   text,                    -- official winner; null = no winner (tie / co-champions)
    scores_known  boolean not null default true,  -- false = workbook scores are placeholders, keep the winner only
    reason        text    not null
);
comment on table core.game_override is
    'Hand-entered official results the loader applies on top of the workbook (2022 co-champions, 2020 placeholder scores). Lives in the seed file.';


-- =====================================================================
-- 4. SLEEPER WEEKLY DETAIL (exactly what Sleeper reports)
-- =====================================================================

create table if not exists core.team_week (
    season               smallint     not null,
    week                 smallint     not null check (week between 1 and 18),
    franchise_code       text         not null,
    sleeper_roster_id    smallint,
    matchup_id           smallint,     -- rows sharing season+week+matchup_id played each other; null = no game
    points               numeric(7,2) not null,   -- Sleeper 'points'
    custom_points        numeric(7,2), -- Sleeper commissioner override field; null in every sample so far
    empty_starter_slots  smallint     not null default 0,
    source               text         not null default 'sleeper' check (source in ('sleeper', 'manual')),
    primary key (season, week, franchise_code),
    foreign key (season, franchise_code) references core.franchise_season
);
comment on table core.team_week is
    'Each franchise''s score every week exactly as Sleeper reports it. Official results live in core.game.';

create index if not exists ix_team_week_matchup on core.team_week (season, week, matchup_id);


create table if not exists core.player_week (
    season          smallint     not null,
    week            smallint     not null,
    franchise_code  text         not null,
    player_id       text         not null references core.player,
    points          numeric(6,2) not null,   -- from players_points
    is_starter      boolean      not null,
    slot_index      smallint,                -- null when on the bench
    lineup_slot     text         not null,   -- QB, RB, WR, TE, FLEX, K, DEF, or BN (bench and IR)
    primary key (season, week, franchise_code, player_id),
    foreign key (season, week, franchise_code) references core.team_week on delete cascade,
    foreign key (season, slot_index) references core.roster_slot,
    constraint ck_starter_slot check (is_starter = (lineup_slot <> 'BN'))
);
comment on table core.player_week is
    'Every rostered player''s Sleeper fantasy points each week, whether they started, and which lineup slot they filled.';

create index if not exists ix_player_week_player on core.player_week (player_id);


-- =====================================================================
-- 5. PLAYOFFS (Sleeper's bracket, for reference)
-- Official places and bowl names come from core.game.designation.
-- =====================================================================

create table if not exists core.playoff_game (
    season               smallint not null references core.season,
    bracket              text     not null check (bracket in ('winners', 'losers')),
    match_id             smallint not null,  -- Sleeper 'm'
    round                smallint not null,  -- Sleeper 'r'
    week                 smallint,           -- NFL week played (playoff_week_start + round - 1)
    team1_code           text,
    team2_code           text,
    team1_from           text,               -- 'W1' = winner of match 1, 'L2' = loser of match 2
    team2_from           text,
    team1_original_code  text,               -- set when the commish edited the bracket (Sleeper t1_original)
    team2_original_code  text,
    winner_code          text,
    loser_code           text,
    place_decided        smallint,           -- Sleeper 'p' (NOT the league's official place)
    primary key (season, bracket, match_id),
    foreign key (season, team1_code)          references core.franchise_season,
    foreign key (season, team2_code)          references core.franchise_season,
    foreign key (season, team1_original_code) references core.franchise_season,
    foreign key (season, team2_original_code) references core.franchise_season,
    foreign key (season, winner_code)         references core.franchise_season,
    foreign key (season, loser_code)          references core.franchise_season
);
comment on table core.playoff_game is
    'Sleeper''s winners and losers brackets as Sleeper ran them. Reference only: the league''s official postseason is in core.game.';


-- =====================================================================
-- 6. DRAFTS AND KEEPERS
-- =====================================================================

create table if not exists core.draft (
    draft_id    text     primary key,   -- Sleeper draft_id ('workbook-2020' style for the 2020 draft)
    season      smallint not null references core.season,
    draft_type  text,                   -- snake, linear, auction
    rounds      smallint,
    status      text,
    started_at  timestamptz,
    source      text     not null default 'sleeper' check (source in ('sleeper', 'sheet', 'manual'))
);
comment on table core.draft is 'One row per league draft.';


create table if not exists core.draft_pick (
    draft_id        text     not null references core.draft,
    pick_no         smallint not null,   -- overall pick number
    season          smallint not null,
    round           smallint not null,
    draft_slot      smallint,
    franchise_code  text     not null,   -- team that made the pick
    player_id       text     references core.player,
    is_keeper       boolean  not null default false,
    primary key (draft_id, pick_no),
    foreign key (season, franchise_code) references core.franchise_season
);
comment on table core.draft_pick is 'Every draft pick. Sleeper places keepers in the last rounds, so round is not a keeper cost.';


create table if not exists core.keeper (
    season           smallint not null,
    franchise_code   text     not null,
    player_id        text     not null references core.player,
    name_as_entered  text,              -- name exactly as typed in the workbook
    notes            text,
    primary key (season, franchise_code, player_id),
    foreign key (season, franchise_code) references core.franchise_season
);
comment on table core.keeper is 'Players kept each season. Imported from the workbook''s Keepers sheet.';


-- =====================================================================
-- 7. TRANSACTIONS
-- =====================================================================

create table if not exists core.transaction (
    transaction_id     text        primary key,
    season             smallint    not null references core.season,
    week               smallint    not null,   -- Sleeper 'leg'
    type               text        not null,   -- trade, waiver, free_agent, commissioner
    status             text        not null,   -- complete, failed (failed waiver bids are kept on purpose)
    created_at         timestamptz not null,
    status_updated_at  timestamptz,
    creator_owner_id   integer     references core.owner,
    waiver_bid         smallint,
    notes              text
);
comment on table core.transaction is 'Every Sleeper transaction, including failed waiver claims.';

create index if not exists ix_transaction_season_week on core.transaction (season, week);


create table if not exists core.transaction_player (
    transaction_id  text not null references core.transaction on delete cascade,
    player_id       text not null references core.player,
    action          text not null check (action in ('add', 'drop')),
    franchise_code  text not null references core.franchise,
    primary key (transaction_id, player_id, action)
);
comment on table core.transaction_player is 'Players added or dropped in each transaction, and by which franchise.';


create table if not exists core.transaction_pick (
    transaction_id  text     not null references core.transaction on delete cascade,
    pick_season     smallint not null,
    pick_round      smallint not null,
    original_code   text     not null references core.franchise,  -- Sleeper roster_id: whose pick it originally was
    from_code       text     not null references core.franchise,  -- Sleeper previous_owner_id (a roster_id, not a user)
    to_code         text     not null references core.franchise,  -- Sleeper owner_id (also a roster_id)
    primary key (transaction_id, pick_season, pick_round, original_code)
);
comment on table core.transaction_pick is 'Draft picks that changed hands in a trade.';


create table if not exists core.transaction_faab (
    transaction_id  text     not null references core.transaction on delete cascade,
    from_code       text     not null references core.franchise,
    to_code         text     not null references core.franchise,
    amount          smallint not null,
    primary key (transaction_id, from_code, to_code)
);
comment on table core.transaction_faab is 'FAAB sent between teams in a trade.';


-- =====================================================================
-- 8. AWARDS AND POWER RANKINGS (from the workbook)
-- =====================================================================

create table if not exists core.award (
    award_id                integer generated always as identity primary key,
    season                  smallint not null references core.season,
    award_name              text     not null,  -- 'First Team All-Fantasy', 'Fantasy Bowl', 'Commissioners Cup'...
    franchise_code          text     references core.franchise,
    player_id               text     references core.player,
    player_name_as_entered  text,               -- null for team awards
    position                text,
    conference              text,               -- team awards: AFC / NFC
    award_group             text,               -- team awards: division name, or In-Season Tournament group
    is_traveling_trophy     boolean  not null default false,
    notes                   text,
    constraint uq_award unique nulls not distinct (season, award_name, franchise_code, player_name_as_entered, award_group)
);
comment on table core.award is 'Individual and team awards by season, from the workbook''s award sheets.';


create table if not exists core.power_ranking (
    season          smallint not null references core.season,
    week            smallint not null,   -- ranking published before this week's games; week 1 = preseason
    rank            smallint not null check (rank between 1 and 4),
    franchise_code  text     not null,
    primary key (season, week, rank),
    constraint uq_power_ranking_team unique (season, week, franchise_code),
    foreign key (season, franchise_code) references core.franchise_season
);
comment on table core.power_ranking is 'Weekly top-4 power rankings from the workbook. Week 1 is the preseason ranking.';
