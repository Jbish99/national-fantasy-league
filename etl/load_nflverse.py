"""
etl/load_nflverse.py

Phase 3: real NFL stat lines, NFL games (results + weather) and the Sleeper -> NFL id link,
from nflverse (free, no key) via the nflreadpy package. Phase 7b added snaps, injuries, expected
points and play-by-play summaries.

Steps, in order (each one is its own transaction):
  download  save nflverse files to data/cache/nflverse/ (the raw layer for this source)
            and log each download in raw.nflverse_pull. By default it downloads files that
            are missing plus everything that can still change (the ID crosswalk, the
            schedule, and this season's stats); older seasons come from the saved files.
  xref      core.player_xref: Sleeper player id -> NFL gsis id
  games     core.nfl_game: every NFL game with scores, roof, surface, temperature, wind
  stats     core.nfl_player_week: weekly stat lines for every QB, RB, WR, TE, K and FB,
            plus anyone who was ever on a league roster
  teams     core.nfl_team_week: each NFL team's weekly defense / special-teams stat line
            (the real stat line behind Sleeper's DEF players)
  snaps        core.nfl_snap_week: offensive / special-teams snaps per player-week (Phase 7b)
  injuries     core.nfl_injury_week: the weekly NFL injury report (Out / Doubtful / Questionable)
  opportunity  core.nfl_opportunity_week: expected stats from usage (nflverse ff_opportunity)
  pbp          play-by-play -> core.nfl_usage_week, core.nfl_team_offense_week, core.nfl_def_week
               (the raw plays, ~13 MB a season, stay in data/cache/nflverse/; see etl/pbp_aggregates.py)
Regular season only for the Phase 7b data. If one of those files can't be downloaded (e.g. nflverse
hasn't published a brand-new season yet), the loader says so and carries on with the rest.
The daily run rebuilds only the current season of the Phase 7b tables (finished seasons don't change);
--refresh or --step rebuilds every season.

How a Sleeper player gets an NFL id (core.player_xref.method):
  crosswalk      nflverse's ID crosswalk (covers ~99% of league players)
  sleeper        Sleeper's own gsis_id, when the crosswalk doesn't have the player
  stats_checked  the two disagree; picks the id whose stat lines carry this player's name
  stats_name     neither has an id (e.g. brand-new rookies); a unique name + position match
                 in the stat lines (league players only)
  manual         data/manual/nfl_id_overrides.csv (player_id, gsis_id, note), always wins

Safe to re-run: every step replaces what it owns.

Run from the project root:
    python -m etl.load_nflverse                  # everything
    python -m etl.load_nflverse --refresh        # re-download every file, then load
    python -m etl.load_nflverse --no-download    # rebuild from the saved files only
    python -m etl.load_nflverse --step teams     # one step (repeatable; download still runs first)
    python -m etl.load_nflverse --step pbp --step snaps
Then check:  select * from qa.summary;
"""

import argparse
import csv
import hashlib
import math
import sys
from collections import Counter, defaultdict
from datetime import date

import psycopg

try:
    import nflreadpy as nfl
    import polars as pl
except ImportError:
    print("nflreadpy isn't installed yet. Run:  python -m pip install -r requirements.txt")
    sys.exit(1)

from etl.common import LoadProblem
from etl.db import PROJECT_ROOT, get_connection
from etl.precompute import data_changed, data_changing
from etl.match_players import normalize
from etl.nfl_columns import (CONTEXT_COLUMNS, GAME_COLUMNS, OPPORTUNITY_COLUMNS, SLEEPER_DEF_IDS, STAT_COLUMNS,
                             TEAM_DEFENSE_COLUMNS)
from etl.pbp_aggregates import PBP_COLUMNS, defense_events, player_usage, regular_season, team_offense

CACHE = PROJECT_ROOT / "data" / "cache" / "nflverse"
OVERRIDES = PROJECT_ROOT / "data" / "manual" / "nfl_id_overrides.csv"
KEEP_POSITIONS = {"QB", "RB", "WR", "TE", "K", "FB"}
FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K"}
SCHEMA_TIP = (
    "Your database is missing a table this code needs. Run:  python -m etl.run_sql --all\n"
    "then run this command again."
)


# ---------------------------------------------------------------------------
# Download (raw layer = files on this computer + a log row per download)
# ---------------------------------------------------------------------------

def season_range(conn):
    first, last = conn.execute("select min(season), max(season) from core.season").fetchone()
    if first is None:
        raise LoadProblem("download", ["core.season is empty. Run the seed file and load_core first."])
    return list(range(first, last + 1)), last


def file_plan(seasons):
    """(dataset, season, file name, how to fetch it, optional). season None = one file for all seasons.
    optional = a failed download only prints a note (Phase 7b extras), instead of stopping the load."""
    plan = [("ff_playerids", None, "ff_playerids.parquet", lambda: nfl.load_ff_playerids(), False),
            ("schedules", None, "schedules.parquet", lambda: nfl.load_schedules(seasons), False),
            ("players", None, "players.parquet", lambda: nfl.load_players(), True)]
    for s in seasons:
        plan.append(("player_stats", s, f"player_stats_{s}.parquet",
                     lambda s=s: nfl.load_player_stats([s], summary_level="week"), False))
        plan.append(("team_stats", s, f"team_stats_{s}.parquet",
                     lambda s=s: nfl.load_team_stats([s], summary_level="week"), False))
        plan.append(("snap_counts", s, f"snap_counts_{s}.parquet", lambda s=s: nfl.load_snap_counts([s]), True))
        plan.append(("injuries", s, f"injuries_{s}.parquet", lambda s=s: nfl.load_injuries([s]), True))
        plan.append(("ff_opportunity", s, f"ff_opportunity_{s}.parquet",
                     lambda s=s: nfl.load_ff_opportunity([s], stat_type="weekly"), True))
        plan.append(("pbp", s, f"pbp_{s}.parquet", lambda s=s: nfl.load_pbp([s]), True))
    return plan


def download(conn, mode):
    """mode: 'auto' (missing + still-changing files), 'all' (--refresh), 'none' (--no-download)."""
    seasons, current = season_range(conn)
    CACHE.mkdir(parents=True, exist_ok=True)
    problems = []
    for dataset, season, name, fetch, optional in file_plan(seasons):
        path = CACHE / name
        if mode == "none":
            if not path.exists() and not optional:
                problems.append(f"{name} isn't saved in data/cache/nflverse yet; run without --no-download")
            continue
        if mode == "auto" and path.exists() and season is not None and season != current:
            continue                                   # a finished season we already have
        try:
            df = fetch()
        except Exception as error:                     # network hiccup: fall back to the saved copy if any
            if path.exists():
                print(f"    note: couldn't re-download {name} ({type(error).__name__}); using the saved copy")
                continue
            if optional:
                print(f"    note: couldn't download {name} ({type(error).__name__}); skipped for now")
                continue
            problems.append(f"couldn't download {name}: {type(error).__name__}: {error}")
            continue
        tmp = path.with_name(path.name + ".tmp")
        df.write_parquet(tmp)
        tmp.replace(path)
        conn.execute(
            "insert into raw.nflverse_pull (dataset, season, file_name, row_count, column_count, file_sha256, "
            "package_version) values (%s, %s, %s, %s, %s, %s, %s)",
            (dataset, season, name, df.height, df.width, hashlib.sha256(path.read_bytes()).hexdigest(),
             getattr(nfl, "__version__", None)),
        )
        print(f"    {name}: {df.height:,} rows")
    if problems:
        raise LoadProblem("download", problems)
    if mode == "none":
        print("    using the saved files only")


def read(name):
    path = CACHE / name
    if not path.exists():
        raise LoadProblem("load", [f"{name} isn't saved in data/cache/nflverse. Run without --no-download."])
    return pl.read_parquet(path)


def read_if_saved(name, columns=None):
    """A Phase 7b file, or None (with a note) when it isn't saved yet."""
    path = CACHE / name
    if not path.exists():
        print(f"    note: {name} isn't saved yet; skipped")
        return None
    if columns:
        available = set(pl.read_parquet_schema(path))
        return pl.read_parquet(path, columns=[c for c in columns if c in available])
    return pl.read_parquet(path)


def regular_games(season):
    """Played regular-season games of one season from the saved schedule."""
    games = read("schedules.parquet")
    return games.filter((pl.col("season") == season) & (pl.col("game_type") == "REG")
                        & pl.col("home_score").is_not_null()).select(
        "game_id", pl.col("week").cast(pl.Int64), "home_team", "away_team",
        pl.col("home_score").cast(pl.Int64), pl.col("away_score").cast(pl.Int64))


def all_stats(conn, columns=None):
    seasons, _ = season_range(conn)
    frames = [read(f"player_stats_{s}.parquet") for s in seasons]
    frames = [f.select([c for c in columns if c in f.columns]) if columns else f for f in frames]
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


# ---------------------------------------------------------------------------
# Value clean-up
# ---------------------------------------------------------------------------

def clean_id(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]                               # 4866.0 -> 4866
    return text or None


def cleaner(sql_type, column):
    """Function that turns a value from the file into what the database column expects."""
    def clean(value):
        if value is None:
            return None
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return None
        if sql_type in ("integer", "smallint"):
            if isinstance(value, float):
                if not value.is_integer():
                    raise ValueError(f"{column} has a decimal value ({value}) but the table expects whole numbers")
                return int(value)
            return int(value)
        if sql_type == "boolean":
            return bool(int(value)) if not isinstance(value, bool) else value
        if sql_type == "date":
            return date.fromisoformat(str(value)[:10])
        return value
    return clean


def copy_rows(conn, table, columns, rows):
    with conn.cursor() as cur:
        with cur.copy(f"copy {table} ({', '.join(columns)}) from stdin") as copy:
            for row in rows:
                copy.write_row(row)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def load_xref(conn):
    ids = read("ff_playerids.parquet")
    for needed in ("sleeper_id", "gsis_id", "name", "position"):
        if needed not in ids.columns:
            raise LoadProblem("xref", [f"the ID crosswalk has no '{needed}' column anymore; run etl.check_nflverse"])
    stats = all_stats(conn, ["player_id", "player_display_name", "position"])

    # What the stat lines say about each NFL id: its name(s) and position(s)
    names_by_gsis, by_name = defaultdict(set), defaultdict(set)
    for gsis, name, position in stats.unique().iter_rows():
        if gsis and name:
            names_by_gsis[gsis].add(normalize(name))
            by_name[normalize(name)].add((gsis, position))

    # The crosswalk: Sleeper id -> [(gsis, name, position, db_season)]
    crosswalk = defaultdict(list)
    cols = ["sleeper_id", "gsis_id", "name", "position"] + (["db_season"] if "db_season" in ids.columns else [])
    for row in ids.select(cols).iter_rows():
        sid, gsis = clean_id(row[0]), clean_id(row[1])
        if sid and gsis:
            crosswalk[sid].append((gsis, row[2], row[3], row[4] if len(row) > 4 else None))

    league = {pid for (pid,) in conn.execute("select distinct player_id from core.player_week")}
    players = conn.execute(
        "select player_id, full_name, position, gsis_id from core.player where full_name is not null").fetchall()

    links, counts = {}, Counter()
    for pid, name, position, sleeper_gsis in players:
        sleeper_gsis = clean_id(sleeper_gsis)
        options = crosswalk.get(pid, [])
        note, cw = None, None
        distinct = sorted({o[0] for o in options})
        if len(distinct) > 1:
            def score(o):
                return (normalize(o[1]) == normalize(name), o[2] == position, o[0] in names_by_gsis, o[3] or 0)
            cw = max(options, key=score)[0]
            note = f"the crosswalk lists this Sleeper id {len(distinct)} times ({', '.join(distinct)}); kept {cw}"
        elif distinct:
            cw = distinct[0]

        if cw and sleeper_gsis and cw != sleeper_gsis:
            named = [g for g in (cw, sleeper_gsis) if normalize(name) in names_by_gsis.get(g, set())]
            if len(named) == 1:
                gsis, method = named[0], "stats_checked"
                note = (f"crosswalk says {cw}, Sleeper says {sleeper_gsis}; "
                        f"the stat lines under {gsis} are {name}")
            else:
                gsis, method = cw, "crosswalk"
                note = (f"crosswalk says {cw}, Sleeper says {sleeper_gsis}; "
                        "the stat lines don't settle it, kept the crosswalk's (please check)")
        elif cw:
            gsis, method = cw, "crosswalk"
        elif sleeper_gsis:
            gsis, method = sleeper_gsis, "sleeper"
        elif pid in league and position in FANTASY_POSITIONS:
            found = {g for g, pos in by_name.get(normalize(name), set())
                     if pos == position or {pos, position} == {"RB", "FB"}}
            if len(found) != 1:
                continue                                       # qa.league_players_without_nfl_id lists it
            gsis, method = found.pop(), "stats_name"
            note = "no id in the crosswalk or from Sleeper; matched by name + position in the stat lines"
        else:
            continue
        links[pid] = (pid, gsis, method, note)
        counts[method] += 1

    known = {pid for pid, *_ in players}
    problems = []
    if OVERRIDES.exists():
        with OVERRIDES.open(newline="", encoding="utf-8-sig") as f:
            for line, row in enumerate(csv.DictReader(f), start=2):
                pid, gsis = clean_id(row.get("player_id")), clean_id(row.get("gsis_id"))
                if not pid and not gsis:
                    continue
                if pid not in known:
                    problems.append(f"nfl_id_overrides.csv line {line}: Sleeper player_id '{pid}' isn't in core.player")
                    continue
                if not gsis:
                    problems.append(f"nfl_id_overrides.csv line {line}: gsis_id is blank")
                    continue
                links[pid] = (pid, gsis, "manual", (row.get("note") or "").strip() or None)
                counts["manual"] += 1
    if problems:
        raise LoadProblem("xref", problems)

    conn.execute("delete from core.player_xref")
    copy_rows(conn, "core.player_xref", ["player_id", "gsis_id", "method", "note"], links.values())
    linked_league = sum(1 for pid in league if pid in links)
    league_people = conn.execute(
        "select count(distinct pw.player_id) from core.player_week pw join core.player p using (player_id) "
        "where p.position is distinct from 'DEF'").fetchone()[0]
    print(f"    {len(links):,} Sleeper players linked to an NFL id ("
          + ", ".join(f"{n:,} {m}" for m, n in counts.most_common()) + ")")
    print(f"    league players (defenses aside): {linked_league:,} of {league_people:,} linked")


def load_games(conn):
    games = read("schedules.parquet")
    missing = [src for _, src, _ in GAME_COLUMNS if src not in games.columns]
    if "game_id" in missing:
        raise LoadProblem("games", ["the schedule file has no game_id column; run etl.check_nflverse"])
    for src in missing:
        games = games.with_columns(pl.lit(None).alias(src))
    cleaners = [cleaner(t, c) for c, _, t in GAME_COLUMNS]
    rows, problems = [], []
    for raw_row in games.select([src for _, src, _ in GAME_COLUMNS]).iter_rows():
        try:
            rows.append([f(v) for f, v in zip(cleaners, raw_row)])
        except ValueError as error:
            problems.append(f"game {raw_row[0]}: {error}")
    if problems:
        raise LoadProblem("games", problems)
    seasons = sorted({r[1] for r in rows})
    conn.execute("delete from core.nfl_game where season = any(%s)", (seasons,))
    copy_rows(conn, "core.nfl_game", [c for c, _, _ in GAME_COLUMNS], rows)
    played = sum(1 for r in rows if r[10] is not None)
    print(f"    {len(rows):,} NFL games ({played:,} played) for {seasons[0]}-{seasons[-1]}")
    if missing:
        print(f"    note: not in the file anymore, left empty: {', '.join(missing)}")


def load_stats(conn):
    seasons, _ = season_range(conn)
    league_ids = {g for (g,) in conn.execute(
        "select distinct x.gsis_id from core.player_xref x join core.player_week pw using (player_id)")}
    table_cols = [c for c, _, _ in CONTEXT_COLUMNS] + [c for c, _ in STAT_COLUMNS]
    source_cols = [s for _, s, _ in CONTEXT_COLUMNS] + [c for c, _ in STAT_COLUMNS]
    cleaners = [cleaner(t, c) for c, _, t in CONTEXT_COLUMNS] + [cleaner(t, c) for c, t in STAT_COLUMNS]
    dropped, missing_seen, problems, total = Counter(), set(), [], 0

    for season in seasons:
        df = read(f"player_stats_{season}.parquet")
        for col in ("player_id", "season", "week", "season_type"):
            if col not in df.columns:
                raise LoadProblem("stats", [f"player_stats_{season}.parquet has no '{col}' column"])
        missing = [c for c in source_cols if c not in df.columns]
        missing_seen.update(missing)
        for col in missing:
            df = df.with_columns(pl.lit(None).alias(col))
        no_id = df.filter(pl.col("player_id").is_null()).height
        df = df.filter(pl.col("player_id").is_not_null())
        keep = (pl.col("position").is_in(list(KEEP_POSITIONS))
                | pl.col("player_id").is_in(list(league_ids)))
        for pos, n in df.filter(~keep).group_by("position").len().iter_rows():
            dropped[pos or "?"] += n
        df = df.filter(keep)
        dupes = df.group_by("player_id", "week").len().filter(pl.col("len") > 1)
        if dupes.height:
            problems.append(f"{season}: {dupes.height} player-weeks appear twice, e.g. "
                            f"{dupes.head(3).select('player_id', 'week').rows()}")
            continue
        rows = []
        for raw_row in df.select(source_cols).iter_rows():
            try:
                row = [f(v) for f, v in zip(cleaners, raw_row)]
            except ValueError as error:
                problems.append(f"{season}: {error}")
                break
            row[0] = clean_id(row[0])
            rows.append(row)
        if problems:
            continue
        conn.execute("delete from core.nfl_player_week where season = %s", (season,))
        copy_rows(conn, "core.nfl_player_week", table_cols, rows)
        weeks = sorted({r[2] for r in rows})
        total += len(rows)
        print(f"    {season}: {len(rows):,} stat lines, weeks {weeks[0]}-{weeks[-1]}"
              + (f" ({no_id} rows without a player id skipped)" if no_id else ""))
    if problems:
        raise LoadProblem("stats", problems)
    print(f"    {total:,} stat lines in all. Left out (not fantasy positions): "
          + ", ".join(f"{p} {n:,}" for p, n in dropped.most_common(8)))
    if missing_seen:
        print(f"    note: columns not in nflverse's files, left empty: {', '.join(sorted(missing_seen))}")


def load_teams(conn):
    seasons, _ = season_range(conn)
    defense = [c for c, _ in TEAM_DEFENSE_COLUMNS]
    cleaners = [cleaner(t, c) for c, t in TEAM_DEFENSE_COLUMNS]
    table_cols = (["team", "season", "week", "season_type", "game_id", "opponent_team", "sleeper_def_id"] + defense
                  + ["opp_passing_yards", "opp_sack_yards_lost", "opp_rushing_yards", "net_yards_allowed"])
    total, problems = 0, []
    for season in seasons:
        df = read(f"team_stats_{season}.parquet")
        needed = ["team", "season", "week", "season_type", "game_id", "opponent_team",
                  "passing_yards", "sack_yards_lost", "rushing_yards"]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            raise LoadProblem("teams", [f"team_stats_{season}.parquet has no {', '.join(missing)} column(s)"])
        for col in defense:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))
        # what each team's opponent gained = that opponent's own offense row in the same game
        offense = df.select(pl.col("game_id"), pl.col("team").alias("opponent_team"),
                            pl.col("passing_yards").alias("opp_passing_yards"),
                            pl.col("sack_yards_lost").alias("opp_sack_yards_lost"),
                            pl.col("rushing_yards").alias("opp_rushing_yards"))
        df = df.join(offense, on=["game_id", "opponent_team"], how="left")
        if df.group_by("team", "week").len().filter(pl.col("len") > 1).height:
            problems.append(f"{season}: a team appears twice in one week")
            continue
        rows = []
        for r in df.select(["team", "season", "week", "season_type", "game_id", "opponent_team"] + defense
                           + ["opp_passing_yards", "opp_sack_yards_lost", "opp_rushing_yards"]).iter_rows():
            try:
                stats = [f(v) for f, v in zip(cleaners, r[6:6 + len(defense)])]
            except ValueError as error:
                problems.append(f"{season}: {error}")
                break
            opp = r[6 + len(defense):]
            net = None if None in opp else opp[0] + opp[1] + opp[2]
            rows.append([r[0], r[1], r[2], r[3], r[4], r[5], SLEEPER_DEF_IDS.get(r[0], r[0])] + stats + list(opp) + [net])
        if problems:
            continue
        conn.execute("delete from core.nfl_team_week where season = %s", (season,))
        copy_rows(conn, "core.nfl_team_week", table_cols, rows)
        total += len(rows)
    if problems:
        raise LoadProblem("teams", problems)
    linked = conn.execute(
        "select count(distinct p.player_id) from core.player p "
        "where p.position = 'DEF' and exists (select 1 from core.nfl_team_week t where t.sleeper_def_id = p.player_id)"
    ).fetchone()[0]
    print(f"    {total:,} team-weeks for {seasons[0]}-{seasons[-1]}; {linked} Sleeper defenses linked")


# ---------------------------------------------------------------------------
# Phase 7b steps: snaps, injuries, expected points, play-by-play summaries
# ---------------------------------------------------------------------------

RELOAD_ALL = False   # set by --refresh or --step: rebuild every season of the Phase 7b tables


def seasons_to_load(conn, table):
    """Phase 7b tables: finished seasons don't change, so the daily run only rebuilds the current season
    (plus any season the table doesn't have yet). --refresh or --step rebuilds them all."""
    seasons, current = season_range(conn)
    if RELOAD_ALL:
        return seasons
    have = {s for (s,) in conn.execute(f"select distinct season from {table}")}
    return [s for s in seasons if s == current or s not in have]


def replace_season(conn, table, season, columns, rows):
    conn.execute(f"delete from {table} where season = %s", (season,))
    copy_rows(conn, table, columns, rows)


def load_snaps(conn):
    seasons = seasons_to_load(conn, "core.nfl_snap_week")
    players = read_if_saved("players.parquet", ["gsis_id", "pfr_id"])
    pfr_to_gsis = {}
    if players is not None:
        for gsis, pfr in players.drop_nulls().iter_rows():
            pfr_to_gsis[pfr.strip()] = gsis.strip()
    to_int, to_pct = cleaner("smallint", "snaps"), cleaner("numeric", "pct")
    columns = ["gsis_id", "season", "week", "game_id", "team", "position", "pfr_player_id", "offense_snaps",
               "offense_pct", "st_snaps", "st_pct", "link_method"]
    for season in seasons:
        df = read_if_saved(f"snap_counts_{season}.parquet")
        if df is None:
            continue
        df = df.filter((pl.col("game_type") == "REG") & pl.col("position").is_in(list(KEEP_POSITIONS)))
        # fallback for players nflverse's player list can't link: same week + team + name in the stat lines
        by_name = defaultdict(set)
        for gsis, name, team, week in read(f"player_stats_{season}.parquet").select(
                "player_id", "player_display_name", "team", "week").iter_rows():
            if gsis and name:
                by_name[(week, team, normalize(name))].add(gsis)
        rows, methods, unlinked = {}, Counter(), 0
        for r in df.iter_rows(named=True):
            pfr = (r["pfr_player_id"] or "").strip() or None
            gsis, method = pfr_to_gsis.get(pfr), "pfr_id"
            if not gsis:
                found = by_name.get((r["week"], r["team"], normalize(r["player"] or "")), set())
                if len(found) != 1:
                    unlinked += 1
                    continue
                gsis, method = next(iter(found)), "name"
            snaps = to_int(r["offense_snaps"]) or 0
            key = (gsis, r["week"])
            if key in rows and (rows[key][7] or 0) >= snaps:
                continue                                     # listed twice: keep the row with more snaps
            rows[key] = [gsis, season, r["week"], r["game_id"], r["team"], r["position"], pfr,
                         to_int(r["offense_snaps"]), to_pct(r["offense_pct"]), to_int(r["st_snaps"]),
                         to_pct(r["st_pct"]), method]
            methods[method] += 1
        replace_season(conn, "core.nfl_snap_week", season, columns, rows.values())
        print(f"    {season}: {len(rows):,} player-weeks ({methods['pfr_id']:,} by id, {methods['name']:,} by name"
              + (f", {unlinked} couldn't be linked" if unlinked else "") + ")")


def load_injuries(conn):
    seasons = seasons_to_load(conn, "core.nfl_injury_week")
    league_ids = {g for (g,) in conn.execute(
        "select distinct x.gsis_id from core.player_xref x join core.player_week pw using (player_id)")}
    severity = {"Out": 3, "Doubtful": 2, "Questionable": 1}
    columns = ["gsis_id", "season", "week", "team", "position", "report_status", "report_primary_injury",
               "report_secondary_injury", "practice_status"]
    text = cleaner("text", "injury")
    for season in seasons:
        df = read_if_saved(f"injuries_{season}.parquet")
        if df is None:
            continue
        kind = "game_type" if "game_type" in df.columns else "season_type"
        df = df.filter((pl.col(kind) == "REG") & pl.col("gsis_id").is_not_null()
                       & (pl.col("position").is_in(list(KEEP_POSITIONS)) | pl.col("gsis_id").is_in(list(league_ids))))
        rows = {}
        for r in df.iter_rows(named=True):
            key = (r["gsis_id"].strip(), int(r["week"]))
            row = [key[0], season, key[1], text(r["team"]), text(r["position"]), text(r["report_status"]),
                   text(r.get("report_primary_injury")), text(r.get("report_secondary_injury")),
                   text(r.get("practice_status"))]
            if key in rows and severity.get(rows[key][5], 0) >= severity.get(row[5], 0):
                continue                                     # listed twice: keep the more serious status
            rows[key] = row
        replace_season(conn, "core.nfl_injury_week", season, columns, rows.values())
        listed = Counter(r[5] for r in rows.values())
        print(f"    {season}: {len(rows):,} player-weeks on the report "
              f"({listed['Out']:,} Out, {listed['Doubtful']:,} Doubtful, {listed['Questionable']:,} Questionable)")


def load_opportunity(conn):
    seasons = seasons_to_load(conn, "core.nfl_opportunity_week")
    to_float = cleaner("double precision", "expected")
    columns = ["gsis_id", "season", "week", "game_id", "team", "position"] + OPPORTUNITY_COLUMNS
    for season in seasons:
        df = read_if_saved(f"ff_opportunity_{season}.parquet")
        if df is None:
            continue
        needed = ["player_id", "week", "game_id", "posteam", "position"]
        if [c for c in needed if c not in df.columns]:
            raise LoadProblem("opportunity", [f"ff_opportunity_{season}.parquet has no "
                                              f"{', '.join(c for c in needed if c not in df.columns)} column(s)"])
        missing = [c for c in OPPORTUNITY_COLUMNS if c not in df.columns]
        for col in missing:
            df = df.with_columns(pl.lit(None).alias(col))
        regular = set(regular_games(season)["game_id"].to_list())
        df = df.filter(pl.col("player_id").is_not_null() & pl.col("game_id").is_in(list(regular)))
        rows = {}
        for r in df.select(needed + OPPORTUNITY_COLUMNS).iter_rows():
            key = (r[0].strip(), int(r[1]))
            if key not in rows:
                rows[key] = [key[0], season, key[1], r[2], r[3], r[4]] + [to_float(v) for v in r[5:]]
        replace_season(conn, "core.nfl_opportunity_week", season, columns, rows.values())
        print(f"    {season}: {len(rows):,} player-weeks"
              + (f" (not in the file, left empty: {', '.join(missing)})" if missing else ""))


def load_pbp(conn):
    seasons = seasons_to_load(conn, "core.nfl_def_week")
    for season in seasons:
        raw = read_if_saved(f"pbp_{season}.parquet", PBP_COLUMNS)
        if raw is None:
            continue
        try:
            pbp = regular_season(raw)
        except ValueError as error:
            raise LoadProblem("pbp", [f"pbp_{season}.parquet: {error}"])
        usage, offense = player_usage(pbp), team_offense(pbp)
        defense = defense_events(pbp, regular_games(season))
        replace_season(conn, "core.nfl_usage_week", season,
                       ["gsis_id", "season", "week", "team", "targets_rz", "targets_ez", "targets_deep",
                        "carries_rz", "carries_gl"],
                       ([g, season, w, t, a, b, c, d, e] for g, w, t, a, b, c, d, e in usage.iter_rows()))
        replace_season(conn, "core.nfl_team_offense_week", season,
                       ["team", "season", "week", "game_id", "plays", "dropbacks", "rushes", "neutral_plays",
                        "neutral_dropbacks", "rz_plays"],
                       ([t, season, w, g, *rest] for t, w, g, *rest in offense.iter_rows()))
        replace_season(conn, "core.nfl_def_week", season,
                       ["team", "season", "week", "game_id", "opponent_team", "three_and_outs", "fourth_down_stops",
                        "st_fumble_recoveries", "opponent_points", "points_allowed"],
                       ([t, season, w, *rest] for t, w, *rest in defense.iter_rows()))
        print(f"    {season}: {pbp.height:,} plays -> {usage.height:,} player-weeks of usage, "
              f"{offense.height:,} team offense weeks, {defense.height:,} team defense weeks")


STEPS = [("xref", load_xref), ("games", load_games), ("stats", load_stats), ("teams", load_teams),
         ("snaps", load_snaps), ("injuries", load_injuries), ("opportunity", load_opportunity), ("pbp", load_pbp)]


def main():
    parser = argparse.ArgumentParser(description="Load NFL stat lines, games and player ids from nflverse.")
    parser.add_argument("--step", action="append", choices=[n for n, _ in STEPS],
                        help="run only this step (repeatable). Default: all")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh", action="store_true", help="re-download every file first")
    group.add_argument("--no-download", action="store_true", help="use only the saved files")
    args = parser.parse_args()
    mode = "all" if args.refresh else "none" if args.no_download else "auto"
    global RELOAD_ALL
    RELOAD_ALL = bool(args.refresh or args.step)

    with get_connection() as conn:
        data_changing(conn, "load_nflverse")    # Phase 11: the app stops trusting cached results + copies now
        for name, step in [("download", lambda c: download(c, mode))] + STEPS:
            if args.step and name != "download" and name not in args.step:
                continue
            print(f"{name}...")
            try:
                step(conn)
                conn.commit()
            except LoadProblem as problem:
                conn.rollback()
                print(f"\nStopped at '{name}'. Nothing from this step was saved. Problems:")
                for line in problem.problems[:40]:
                    print(f"  - {line}")
                sys.exit(1)
            except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as error:
                conn.rollback()
                print(f"\nStopped at '{name}'. The database said:\n  {error}\n{SCHEMA_TIP}")
                sys.exit(1)
            except psycopg.Error as error:
                conn.rollback()
                print(f"\nStopped at '{name}'. Nothing from this step was saved. The database said:\n  {error}")
                sys.exit(1)

        data_changed(conn, "load_nflverse")     # Phase 11: new data version + rebuilt precomputed copies
        size = conn.execute(
            "select pg_size_pretty(pg_database_size(current_database())), "
            "pg_size_pretty(pg_total_relation_size('core.nfl_player_week')), "
            "pg_size_pretty(coalesce(sum(pg_total_relation_size(format('core.%I', c.relname)::regclass)), 0)) "
            "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'core' and c.relkind = 'r' and c.relname in "
            "('nfl_snap_week', 'nfl_injury_week', 'nfl_opportunity_week', 'nfl_usage_week', "
            "'nfl_team_offense_week', 'nfl_def_week')").fetchone()
        summary = conn.execute("select check_name, problems from qa.summary where problems > 0").fetchall()
    print(f"\nDatabase size: {size[0]} (stat lines: {size[1]}; snaps, injuries, expected points and "
          f"play-by-play summaries: {size[2]}). Neon's free plan allows 1 GB.")
    if summary:
        print("These checks found something to look at (select * from qa.<check name>):")
        for check, count in summary:
            print(f"  {check}: {count}")
    else:
        print("All checks in qa.summary are zero.")


if __name__ == "__main__":
    main()
