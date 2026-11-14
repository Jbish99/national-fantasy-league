"""
etl/nfl_columns.py

The real-stat-line columns we keep in core.nfl_player_week, with their database types.
One list shared by the loader (etl/load_nflverse.py) and the schema file
(db/schema/005_nfl_stats.sql), so the two can't drift apart.

Names follow nflverse's weekly player stats. If we ever switch stat sources, the new
loader maps its fields onto these same names and the table stays the same.
Kept: passing, rushing, receiving, fumbles, returns, kicking, and nflverse's own
fantasy points. Left out: individual defensive (IDP) and punting columns.

Types were checked against nflverse's real files for 2020-2026 (2026-11-13): every count is a
whole number (integer); rates and EPA are decimals (double precision); fantasy points are numeric.
If nflverse ever sends a decimal in an integer column, the loader stops and says which column.
"""

STAT_COLUMNS = [
    # passing
    ("completions", "integer"), ("attempts", "integer"), ("passing_yards", "integer"),
    ("passing_tds", "integer"), ("passing_interceptions", "integer"), ("sacks_suffered", "integer"),
    ("sack_yards_lost", "integer"), ("sack_fumbles", "integer"), ("sack_fumbles_lost", "integer"),
    ("passing_air_yards", "integer"), ("passing_yards_after_catch", "integer"),
    ("passing_first_downs", "integer"), ("passing_2pt_conversions", "integer"),
    ("passing_epa", "double precision"), ("passing_cpoe", "double precision"), ("pacr", "double precision"),
    # rushing
    ("carries", "integer"), ("rushing_yards", "integer"), ("rushing_tds", "integer"),
    ("rushing_fumbles", "integer"), ("rushing_fumbles_lost", "integer"), ("rushing_first_downs", "integer"),
    ("rushing_2pt_conversions", "integer"), ("rushing_epa", "double precision"),
    # receiving
    ("receptions", "integer"), ("targets", "integer"), ("receiving_yards", "integer"),
    ("receiving_tds", "integer"), ("receiving_fumbles", "integer"), ("receiving_fumbles_lost", "integer"),
    ("receiving_air_yards", "integer"), ("receiving_yards_after_catch", "integer"),
    ("receiving_first_downs", "integer"), ("receiving_2pt_conversions", "integer"),
    ("receiving_epa", "double precision"), ("racr", "double precision"), ("target_share", "double precision"),
    ("air_yards_share", "double precision"), ("wopr", "double precision"),
    # fumbles, returns, other scoring
    ("fumbles_total", "integer"), ("fumbles_lost_total", "integer"), ("special_teams_tds", "integer"),
    ("fumble_recovery_tds", "integer"), ("punt_returns", "integer"), ("punt_return_yards", "integer"),
    ("kickoff_returns", "integer"), ("kickoff_return_yards", "integer"),
    # kicking
    ("fg_made", "integer"), ("fg_att", "integer"), ("fg_missed", "integer"), ("fg_blocked", "integer"),
    ("fg_long", "integer"), ("fg_made_0_19", "integer"), ("fg_made_20_29", "integer"),
    ("fg_made_30_39", "integer"), ("fg_made_40_49", "integer"), ("fg_made_50_59", "integer"),
    ("fg_made_60_", "integer"), ("fg_missed_0_19", "integer"), ("fg_missed_20_29", "integer"),
    ("fg_missed_30_39", "integer"), ("fg_missed_40_49", "integer"), ("fg_missed_50_59", "integer"),
    ("fg_missed_60_", "integer"), ("fg_made_distance", "integer"), ("pat_made", "integer"),
    ("pat_att", "integer"), ("pat_missed", "integer"), ("pat_blocked", "integer"),
    # nflverse's own fantasy points (standard and full PPR; NOT our league's scoring)
    ("fantasy_points", "numeric"), ("fantasy_points_ppr", "numeric"),
]

# Context columns: (column in core.nfl_player_week, column in nflverse's data, type)
CONTEXT_COLUMNS = [
    ("gsis_id", "player_id", "text"),
    ("season", "season", "smallint"),
    ("week", "week", "smallint"),
    ("season_type", "season_type", "text"),
    ("game_id", "game_id", "text"),
    ("player_name", "player_display_name", "text"),
    ("position", "position", "text"),
    ("position_group", "position_group", "text"),
    ("team", "team", "text"),
    ("opponent_team", "opponent_team", "text"),
]

# core.nfl_game: (column, nflverse schedules column, type)
GAME_COLUMNS = [
    ("game_id", "game_id", "text"), ("season", "season", "smallint"), ("game_type", "game_type", "text"),
    ("week", "week", "smallint"), ("gameday", "gameday", "date"), ("weekday", "weekday", "text"),
    ("gametime", "gametime", "text"), ("away_team", "away_team", "text"), ("home_team", "home_team", "text"),
    ("away_score", "away_score", "smallint"), ("home_score", "home_score", "smallint"),
    ("overtime", "overtime", "boolean"), ("location", "location", "text"), ("roof", "roof", "text"),
    ("surface", "surface", "text"), ("temp", "temp", "smallint"), ("wind", "wind", "smallint"),
    ("stadium_id", "stadium_id", "text"), ("stadium", "stadium", "text"), ("div_game", "div_game", "boolean"),
    ("spread_line", "spread_line", "numeric"), ("total_line", "total_line", "numeric"),
    ("away_moneyline", "away_moneyline", "integer"), ("home_moneyline", "home_moneyline", "integer"),
    ("away_rest", "away_rest", "smallint"), ("home_rest", "home_rest", "smallint"),
    ("away_qb_id", "away_qb_id", "text"), ("home_qb_id", "home_qb_id", "text"),
    ("away_qb_name", "away_qb_name", "text"), ("home_qb_name", "home_qb_name", "text"),
    ("away_coach", "away_coach", "text"), ("home_coach", "home_coach", "text"),
    ("referee", "referee", "text"), ("pfr_game_id", "pfr", "text"), ("espn_game_id", "espn", "text"),
]

# core.nfl_team_week: a team's defense and special-teams stat line each week (from nflverse
# weekly team stats). Types checked against the real 2020-2026 files (2026-11-13).
TEAM_DEFENSE_COLUMNS = [
    ("def_sacks", "numeric"), ("def_sack_yards", "numeric"), ("def_qb_hits", "integer"),
    ("def_interceptions", "integer"), ("def_interception_yards", "integer"), ("def_fumbles_forced", "integer"),
    ("fumble_recovery_opp", "integer"), ("fumble_recovery_tds", "integer"), ("def_tds", "integer"),
    ("def_safeties", "integer"), ("def_tackles_for_loss", "integer"), ("def_pass_defended", "integer"),
    ("def_punt_blocks", "integer"), ("def_fg_blocks", "integer"), ("def_pat_blocks", "integer"),
    ("def_2pt_made", "integer"), ("special_teams_tds", "integer"), ("punt_return_yards", "integer"),
    ("kickoff_return_yards", "integer"),
]

# nflverse team code -> Sleeper's defense player id, where they differ
SLEEPER_DEF_IDS = {"LA": "LAR"}
