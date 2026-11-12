-- =====================================================================
-- db/schema/002_core.sql
-- CORE layer: clean, normalized tables the dashboard reads from.
--
-- Big idea: history belongs to the FRANCHISE (rulebook 3.3), keyed by
-- the brand-registry code (DET, FLT, GRR...). Owners, Sleeper roster_ids
-- and team names hang off franchise_season, so ownership changes,
-- renames and the ESPN-era 2020 season all fit the same shape.
--
-- Natural primary keys everywhere so loaders can upsert safely.
-- Safe to re-run: everything uses IF NOT EXISTS.
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
    'One row per franchise, keyed by brand-registry code. Owns all team history, records and trophies (rulebook 3.3).';


create table if not exists core.owner (
    owner_id         integer generated always as identity primary key,
    sleeper_user_id  text unique,        -- main identity for 2021+
    espn_member_id   text unique,        -- for the 2020 ESPN season, if the sheet has it
    display_name     text,               -- latest Sleeper display name
    real_name        text,               -- fill in by hand
    nickname         text,               -- fill in by hand
    constraint ck_owner_identified
        check (sleeper_user_id is not null or espn_member_id is not null or real_name is not null)
);
comment on table core.owner is
    'People who have run a franchise. Tracked by Sleeper user_id across seasons, plus ESPN id or name for 2020.';


create table if not exists core.season (
    season                      smallint primary key,
    platform                    text     not null check (platform in ('espn', 'sleeper')),
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
    'One row per league season (2020 ESPN, 2021+ Sleeper) with that season''s league-level settings.';


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
    season                  smallint primary key references core.season,
    total_keepers           smallint not null,
    unrestricted_keepers    smallint not null default 0,  -- Captains in 2023-2026
    restricted_keepers      smallint not null default 0,
    restricted_banned_rounds smallint[],                  -- rounds a restricted keeper can't come from
    restricted_costs_pick   boolean  not null default false, -- give up the pick the player was drafted with last year
    notes                   text,
    constraint ck_keeper_counts check (unrestricted_keepers + restricted_keepers = total_keepers)
);
comment on table core.keeper_rule is
    'Keeper rules per season (3 restricted in 2021-22, 2 Captains + 2 restricted in 2023-26). Hand-maintained.';


create table if not exists core.franchise_season (
    season                   smallint not null references core.season,
    franchise_code           text     not null references core.franchise,
    owner_id                 integer  references core.owner,   -- filled by the loader
    sleeper_roster_id        smallint,                         -- only unique within a season
    team_name                text,                             -- name shown that season
    division_number          smallint,
    division_name            text,
    conference               text,
    -- Sleeper's own season totals from /rosters. Used ONLY to validate our numbers.
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
    espn_id            text,              -- helps match the 2020 ESPN season
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
-- 3. WEEKLY RESULTS (the most important tables)
-- =====================================================================

create table if not exists core.team_week (
    season               smallint     not null,
    week                 smallint     not null check (week between 1 and 18),
    franchise_code       text         not null,
    sleeper_roster_id    smallint,
    matchup_id           smallint,     -- rows sharing season+week+matchup_id played each other; null = no game
    points               numeric(7,2) not null,   -- Sleeper 'points'
    custom_points        numeric(7,2),            -- Sleeper commissioner override field; null in every sample so far
    empty_starter_slots  smallint     not null default 0,
    source               text         not null default 'sleeper' check (source in ('sleeper', 'espn', 'manual')),
    primary key (season, week, franchise_code),
    foreign key (season, franchise_code) references core.franchise_season
);
comment on table core.team_week is
    'Each franchise''s score every week. Opponent and W/L come from pairing on matchup_id (see core.v_team_week_result).';

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
    'Every rostered player''s fantasy points each week, whether they started, and which lineup slot they filled.';

create index if not exists ix_player_week_player on core.player_week (player_id);


create table if not exists core.result_override (
    season           smallint     not null,
    week             smallint     not null,
    franchise_code   text         not null,
    official_result  char(1)      not null check (official_result in ('W', 'L', 'T')),
    official_points  numeric(7,2),          -- only if the official score differs from Sleeper
    reason           text         not null, -- e.g. 'Madden tiebreak (rulebook 11.4)'
    primary key (season, week, franchise_code),
    foreign key (season, week, franchise_code) references core.team_week
);
comment on table core.result_override is
    'Rare cases where the official league result differs from Sleeper (rulebook 11.4 and 11.6). One row is enough; the opponent''s result flips automatically. Expected to stay empty.';


-- =====================================================================
-- 4. PLAYOFFS
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
    place_decided        smallint,           -- Sleeper 'p'
    bowl_name            text,               -- league name for the game (Fantasy Bowl, Mud Bowl...), filled by hand
    primary key (season, bracket, match_id),
    foreign key (season, team1_code)          references core.franchise_season,
    foreign key (season, team2_code)          references core.franchise_season,
    foreign key (season, team1_original_code) references core.franchise_season,
    foreign key (season, team2_original_code) references core.franchise_season,
    foreign key (season, winner_code)         references core.franchise_season,
    foreign key (season, loser_code)          references core.franchise_season
);
comment on table core.playoff_game is
    'Winners and losers bracket games from Sleeper, plus league bowl names. Source for champions and final places.';


-- =====================================================================
-- 5. DRAFTS AND KEEPERS
-- =====================================================================

create table if not exists core.draft (
    draft_id    text     primary key,   -- Sleeper draft_id ('espn-2020' style for ESPN)
    season      smallint not null references core.season,
    draft_type  text,                   -- snake, linear, auction
    rounds      smallint,
    status      text,
    started_at  timestamptz,
    source      text     not null default 'sleeper' check (source in ('sleeper', 'espn', 'manual'))
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
    is_keeper       boolean  not null default false,  -- Sleeper flag; keepers sit in the last rounds, so round here is NOT keeper cost
    primary key (draft_id, pick_no),
    foreign key (season, franchise_code) references core.franchise_season
);
comment on table core.draft_pick is 'Every draft pick. Keeper costs live in core.keeper, not here.';


create table if not exists core.keeper (
    season           smallint not null,
    franchise_code   text     not null,
    player_id        text     not null references core.player,
    keeper_type      text     not null check (keeper_type in ('captain', 'restricted', 'unrestricted')),
    cost_round       smallint,          -- round of the pick given up (restricted keepers)
    name_as_entered  text,              -- name exactly as typed in the keeper spreadsheet
    notes            text,
    primary key (season, franchise_code, player_id),
    foreign key (season, franchise_code) references core.franchise_season
);
comment on table core.keeper is
    'Players kept each season and how (Captain vs restricted keeper). Imported from the keeper spreadsheet.';


-- =====================================================================
-- 6. TRANSACTIONS
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
-- 7. AWARDS
-- =====================================================================

create table if not exists core.award (
    award_id                integer generated always as identity primary key,
    season                  smallint not null references core.season,
    award_name              text     not null,  -- 'First Team All-Fantasy', 'Most Valuable Owner'...
    franchise_code          text     references core.franchise,
    player_id               text     references core.player,
    player_name_as_entered  text,
    position                text,
    notes                   text,
    constraint uq_award unique nulls not distinct (season, award_name, franchise_code, player_name_as_entered)
);
comment on table core.award is 'League awards and honors by season, imported from the award history spreadsheet.';
