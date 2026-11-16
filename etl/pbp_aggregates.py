"""
etl/pbp_aggregates.py

Phase 7b: turns one season of nflverse play-by-play (~50k plays, 372 columns) into the three
compact tables that go in the database. The raw play-by-play stays in data/cache/nflverse/.
Used by etl/load_nflverse.py (step 'pbp'). Pure polars, no database.

  player_usage(pbp)            -> core.nfl_usage_week        red-zone / goal-line / end-zone / deep usage
  team_offense(pbp)            -> core.nfl_team_offense_week  plays, dropbacks, designed runs, neutral pass rate
  defense_events(pbp, games)   -> core.nfl_def_week           3-and-outs, 4th-down stops, special-teams fumble
                                                              recoveries, points allowed by the defense

How the DEF pieces were checked (2026-11-16): with the league's real scoring rules they rebuild Sleeper's
DEF points exactly for 28 of 29 real 2025 defense-weeks (weeks 1 and 15 in sleeper_context/); the miss is
half a point (a passes-defended count that differs between stat providers). Two lessons from that check:
  * points allowed leave out points scored while the defense wasn't on the field (pick-six thrown by its
    own offense, kick/punt return TDs, with their PAT or 2-pt; safeties; defensive 2-pt returns)
  * fumbles recovered on kickoff / punt plays score as special-teams recoveries (1 point, not 2)
"""

import polars as pl

# The play-by-play columns these functions read (load only these; the file has 372)
PBP_COLUMNS = [
    "game_id", "play_id", "season", "season_type", "week", "posteam", "defteam", "home_team", "away_team",
    "play_type", "down", "yardline_100", "air_yards", "wp", "half_seconds_remaining",
    "pass_attempt", "rush_attempt", "sack", "qb_dropback", "qb_scramble", "qb_kneel", "qb_spike",
    "two_point_attempt", "receiver_player_id", "rusher_player_id",
    "fixed_drive", "fixed_drive_result", "drive_first_downs", "fourth_down_failed",
    "fumble_lost", "fumble_recovery_1_team", "touchdown", "td_team", "safety", "defensive_two_point_conv",
    "extra_point_attempt", "extra_point_result", "two_point_conv_result",
]


def regular_season(pbp: pl.DataFrame) -> pl.DataFrame:
    missing = [c for c in PBP_COLUMNS if c not in pbp.columns]
    if missing:
        raise ValueError(f"play-by-play has no {', '.join(missing)} column(s) anymore")
    return pbp.filter(pl.col("season_type") == "REG")


def _scrimmage(pbp: pl.DataFrame) -> pl.DataFrame:
    """Real offensive snaps: passes (incl. sacks, scrambles) and runs; no penalty-nullified plays
    (play_type 'no_play'), no 2-point tries, no kneels or spikes."""
    return pbp.filter(pl.col("play_type").is_in(["pass", "run"]) & pl.col("posteam").is_not_null()
                      & (pl.col("two_point_attempt").fill_null(0) == 0)
                      & (pl.col("qb_kneel").fill_null(0) == 0) & (pl.col("qb_spike").fill_null(0) == 0))


def player_usage(pbp: pl.DataFrame) -> pl.DataFrame:
    plays = _scrimmage(pbp)
    targets = plays.filter(pl.col("receiver_player_id").is_not_null() & (pl.col("pass_attempt") == 1)
                           & (pl.col("sack").fill_null(0) == 0))
    t = targets.group_by(pl.col("receiver_player_id").alias("gsis_id"), "week", pl.col("posteam").alias("team")).agg(
        (pl.col("yardline_100") <= 20).sum().alias("targets_rz"),
        (pl.col("air_yards") >= pl.col("yardline_100")).sum().alias("targets_ez"),
        (pl.col("air_yards") >= 20).sum().alias("targets_deep"))
    carries = plays.filter(pl.col("rusher_player_id").is_not_null() & (pl.col("rush_attempt") == 1))
    c = carries.group_by(pl.col("rusher_player_id").alias("gsis_id"), "week", pl.col("posteam").alias("team")).agg(
        (pl.col("yardline_100") <= 20).sum().alias("carries_rz"),
        (pl.col("yardline_100") <= 5).sum().alias("carries_gl"))
    out = t.join(c, on=["gsis_id", "week", "team"], how="full", coalesce=True)
    counts = ["targets_rz", "targets_ez", "targets_deep", "carries_rz", "carries_gl"]
    out = out.with_columns([pl.col(n).fill_null(0).cast(pl.Int64) for n in counts])
    # a player with two teams in one week can't happen in the NFL; if the data ever says so, keep the busier one
    out = out.with_columns(pl.sum_horizontal(counts).alias("_n")).sort("_n", descending=True) \
             .unique(subset=["gsis_id", "week"], keep="first").drop("_n")
    return out.select("gsis_id", "week", "team", *counts)


def team_offense(pbp: pl.DataFrame) -> pl.DataFrame:
    plays = _scrimmage(pbp)
    dropback = pl.col("qb_dropback").fill_null(0) == 1
    designed_run = (pl.col("rush_attempt").fill_null(0) == 1) & (pl.col("qb_scramble").fill_null(0) == 0)
    snap = dropback | designed_run
    neutral = pl.col("down").cast(pl.Int64, strict=False).is_in([1, 2]) & pl.col("wp").is_between(0.2, 0.8) & (pl.col("half_seconds_remaining") > 120)
    return plays.group_by(pl.col("posteam").alias("team"), "week", "game_id").agg(
        snap.sum().alias("plays"), dropback.sum().alias("dropbacks"), designed_run.sum().alias("rushes"),
        (snap & neutral).sum().alias("neutral_plays"), (dropback & neutral).sum().alias("neutral_dropbacks"),
        (snap & (pl.col("yardline_100") <= 20)).sum().alias("rz_plays"))


def non_defense_points(pbp: pl.DataFrame) -> pl.DataFrame:
    """(game_id, team, non_defense_points): points a team scored on plays where the other team's defense
    wasn't on the field: return TDs + their conversion, safeties, defensive 2-point returns."""
    p = pbp.select("game_id", "play_id", "play_type", "posteam", "defteam", "td_team", "touchdown", "safety",
                   "defensive_two_point_conv", "extra_point_attempt", "two_point_attempt", "extra_point_result",
                   "two_point_conv_result").with_columns(pl.col("play_id").cast(pl.Float64)).sort("game_id", "play_id")
    return_td = p.filter((pl.col("touchdown") == 1) & pl.col("td_team").is_not_null()
                         & (pl.col("play_type").is_in(["kickoff", "punt"]) | (pl.col("td_team") != pl.col("posteam"))))
    tries = p.filter((pl.col("extra_point_attempt") == 1) | (pl.col("two_point_attempt") == 1)).select(
        "game_id", "play_id", pl.col("posteam").alias("try_team"),
        pl.when(pl.col("extra_point_result") == "good").then(1)
          .when(pl.col("two_point_conv_result") == "success").then(2).otherwise(0).alias("try_points"))
    td = return_td.sort("game_id", "play_id").join_asof(
        tries.sort("game_id", "play_id"), on="play_id", by="game_id", strategy="forward", check_sortedness=False
    ).with_columns(
        (6 + pl.when(pl.col("try_team") == pl.col("td_team")).then(pl.col("try_points")).otherwise(0)).alias("points"),
        pl.col("td_team").alias("team"))
    safeties = p.filter(pl.col("safety") == 1).with_columns(pl.lit(2).alias("points"), pl.col("defteam").alias("team"))
    def_2pt = p.filter(pl.col("defensive_two_point_conv") == 1).with_columns(pl.lit(2).alias("points"),
                                                                             pl.col("defteam").alias("team"))
    parts = [d.select("game_id", "team", pl.col("points").cast(pl.Int64)) for d in (td, safeties, def_2pt)]
    return pl.concat(parts).group_by("game_id", "team").agg(pl.col("points").sum().alias("non_defense_points"))


def defense_events(pbp: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """games: played regular-season games for this season (game_id, week, home_team, away_team, home_score, away_score)."""
    sides = pl.concat([
        games.select("game_id", "week", pl.col("home_team").alias("team"), pl.col("away_team").alias("opponent_team"),
                     pl.col("away_score").alias("opponent_points")),
        games.select("game_id", "week", pl.col("away_team").alias("team"), pl.col("home_team").alias("opponent_team"),
                     pl.col("home_score").alias("opponent_points")),
    ])
    drives = pbp.filter(pl.col("posteam").is_not_null() & pl.col("fixed_drive").is_not_null()).group_by(
        "game_id", "fixed_drive").agg(pl.col("posteam").first(), pl.col("defteam").first(),
                                      pl.col("fixed_drive_result").first(), pl.col("drive_first_downs").max())
    three_and_outs = drives.filter((pl.col("fixed_drive_result") == "Punt") & (pl.col("drive_first_downs") == 0)) \
                           .group_by("game_id", pl.col("defteam").alias("team")).len("three_and_outs")
    fourth = pbp.filter(pl.col("fourth_down_failed") == 1) \
                .group_by("game_id", pl.col("defteam").alias("team")).len("fourth_down_stops")
    st_fr = pbp.filter(pl.col("play_type").is_in(["kickoff", "punt"]) & (pl.col("fumble_lost") == 1)
                       & pl.col("fumble_recovery_1_team").is_not_null()) \
               .group_by("game_id", pl.col("fumble_recovery_1_team").alias("team")).len("st_fumble_recoveries")
    other = non_defense_points(pbp).rename({"team": "opponent_team"})
    out = (sides.join(three_and_outs, on=["game_id", "team"], how="left")
                .join(fourth, on=["game_id", "team"], how="left")
                .join(st_fr, on=["game_id", "team"], how="left")
                .join(other, on=["game_id", "opponent_team"], how="left"))
    return out.with_columns(
        pl.col("three_and_outs").fill_null(0), pl.col("fourth_down_stops").fill_null(0),
        pl.col("st_fumble_recoveries").fill_null(0),
        (pl.col("opponent_points") - pl.col("non_defense_points").fill_null(0)).alias("points_allowed"),
    ).select("team", "week", "game_id", "opponent_team", "three_and_outs", "fourth_down_stops",
             "st_fumble_recoveries", "opponent_points", "points_allowed")
