"""
etl/check_nflverse.py

Phase 3, step 0: look at what nflverse actually sends before we design tables around it.
Downloads three datasets with nflreadpy (free, no key) and prints their columns, types,
how empty each column is, and a couple of sample rows:

  1. load_ff_playerids()   the ID crosswalk (Sleeper id <-> nflverse gsis id and others)
  2. load_player_stats()   weekly real stat lines (yards, TDs...)
  3. load_schedules()      games, results, roof / temperature / wind

It also measures how many of OUR league's players (anyone on a Sleeper roster) the
crosswalk can map, and whether it agrees with the gsis ids Sleeper itself sends.
Nothing is written to the database.

Run from the project root (takes a minute; it downloads a few files):
    python -m etl.check_nflverse
Then paste the whole output to Claude.
"""

import sys

try:
    import nflreadpy as nfl
    import polars as pl
except ImportError:
    print("nflreadpy isn't installed yet. Run:  python -m pip install -r requirements.txt")
    sys.exit(1)

from etl.db import get_connection

SEASONS = list(range(2020, 2027))


def describe(name, df, key_columns=(), sample_columns=None, max_columns=None):
    print(f"\n{'=' * 78}\n{name}: {df.height:,} rows x {df.width} columns\n{'=' * 78}")
    nulls = df.null_count().row(0)
    columns = list(zip(df.columns, df.dtypes, nulls))
    shown = columns if max_columns is None else columns[:max_columns]
    for col, dtype, n in shown:
        mark = " <-- key" if col in key_columns else ""
        print(f"  {col:32} {str(dtype):14} {100 * n / max(df.height, 1):5.1f}% empty{mark}")
    if max_columns is not None and len(columns) > max_columns:
        rest = [c for c, _, _ in columns[max_columns:]]
        print(f"  ...and {len(rest)} more: {', '.join(rest)}")
    missing = [c for c in key_columns if c not in df.columns]
    if missing:
        print(f"  !! expected but MISSING: {', '.join(missing)}")
    cols = [c for c in (sample_columns or df.columns[:8]) if c in df.columns]
    if cols:
        print("  sample rows:")
        with pl.Config(tbl_cols=-1, tbl_width_chars=200, fmt_str_lengths=30, tbl_formatting="ASCII_MARKDOWN"):
            print(df.select(cols).head(3))


def crosswalk(ids):
    describe("1. load_ff_playerids() - ID crosswalk", ids,
             key_columns=("sleeper_id", "gsis_id", "name", "position"),
             sample_columns=["name", "position", "team", "sleeper_id", "gsis_id", "espn_id", "pfr_id"])
    if not {"sleeper_id", "gsis_id"} <= set(ids.columns):
        return
    print(f"\n  sleeper_id type: {ids.schema['sleeper_id']}  (example values: "
          f"{ids.filter(pl.col('sleeper_id').is_not_null())['sleeper_id'].head(3).to_list()})")
    mapped = ids.filter(pl.col("sleeper_id").is_not_null() & pl.col("gsis_id").is_not_null())
    print(f"  rows with both sleeper_id and gsis_id: {mapped.height:,}")
    dupes = mapped.group_by("sleeper_id").len().filter(pl.col("len") > 1).height
    print(f"  sleeper_ids that appear more than once: {dupes}")

    def as_id(value):  # 4866, 4866.0 and "4866" are the same id
        text = str(value).strip()
        return text[:-2] if text.endswith(".0") else text

    lookup = {as_id(s): str(g).strip() for s, g in mapped.select("sleeper_id", "gsis_id").iter_rows()}
    with get_connection() as conn:
        league = conn.execute(
            """
            select p.player_id, p.full_name, p.position, p.gsis_id, sum(pw.points) as points
            from core.player p join core.player_week pw using (player_id)
            where p.position is distinct from 'DEF'
            group by 1, 2, 3, 4
            """).fetchall()
    found = [r for r in league if r[0] in lookup]
    print(f"\n  OUR LEAGUE: {len(league):,} players have been on a Sleeper roster (defenses excluded)")
    print(f"    the crosswalk maps {len(found):,} of them ({100 * len(found) / max(len(league), 1):.1f}%)")
    with_sleeper_gsis = [r for r in league if r[3]]
    agree = sum(1 for r in with_sleeper_gsis if lookup.get(r[0]) == r[3].strip())
    disagree = [r for r in with_sleeper_gsis if r[0] in lookup and lookup[r[0]] != r[3].strip()]
    print(f"    Sleeper itself sends a gsis_id for {len(with_sleeper_gsis):,}; the crosswalk agrees on {agree:,}, "
          f"disagrees on {len(disagree)}")
    for pid, name, pos, gsis, _ in disagree[:5]:
        print(f"      {name} ({pos}, sleeper {pid}): Sleeper says {gsis.strip()}, crosswalk says {lookup[pid]}")
    unmapped = sorted((r for r in league if r[0] not in lookup and not r[3]), key=lambda r: -(r[4] or 0))
    print(f"    no gsis id from either source: {len(unmapped):,}. Most fantasy points among them:")
    for pid, name, pos, _, points in unmapped[:12]:
        print(f"      {name or '?'} ({pos or '?'}, sleeper {pid}): {points or 0:.1f} pts")


def stats():
    df = nfl.load_player_stats(SEASONS, summary_level="week")
    describe("2. load_player_stats(2020-2026, weekly) - real stat lines", df,
             key_columns=("player_id", "season", "week", "season_type", "team", "opponent_team",
                          "fantasy_points", "fantasy_points_ppr"),
             sample_columns=["player_id", "player_display_name", "position", "season", "week", "season_type",
                             "team", "passing_yards", "rushing_yards", "receiving_yards", "fantasy_points_ppr"],
             max_columns=45)
    if {"season", "week", "season_type"} <= set(df.columns):
        print("\n  weeks per season:")
        summary = (df.group_by("season", "season_type")
                   .agg(pl.col("week").min().alias("first_week"), pl.col("week").max().alias("last_week"),
                        pl.len().alias("rows"))
                   .sort("season", "season_type"))
        with pl.Config(tbl_formatting="ASCII_MARKDOWN", tbl_rows=-1):
            print(summary)


def schedules():
    df = nfl.load_schedules(SEASONS)
    describe("3. load_schedules(2020-2026) - games, results, weather", df,
             key_columns=("game_id", "season", "week", "game_type", "gameday", "home_team", "away_team",
                          "home_score", "away_score", "roof", "surface", "temp", "wind", "stadium"),
             sample_columns=["game_id", "season", "week", "game_type", "gameday", "away_team", "home_team",
                             "away_score", "home_score", "roof", "temp", "wind"])
    if "roof" in df.columns:
        print("\n  roof values:", df["roof"].value_counts().sort("count", descending=True).rows())
    if {"roof", "temp"} <= set(df.columns):
        outdoor = df.filter(pl.col("roof").is_in(["outdoors", "open"]))
        print(f"  outdoor/open-roof games with no temperature: {outdoor['temp'].null_count()} of {outdoor.height}")


def main():
    print(f"nflreadpy {getattr(nfl, '__version__', '?')}, polars {pl.__version__}")
    for label, job in (("crosswalk", lambda: crosswalk(nfl.load_ff_playerids())),
                       ("player stats", stats), ("schedules", schedules)):
        try:
            job()
        except Exception as error:   # keep going so one failed download doesn't hide the others
            print(f"\n!! {label} failed: {type(error).__name__}: {error}")
    print("\nDone. Paste everything above to Claude.")


if __name__ == "__main__":
    main()
