"""
etl/load_nflverse.py

Phase 3: real NFL stat lines, NFL games (results + weather) and the Sleeper -> NFL id link,
from nflverse (free, no key) via the nflreadpy package.

Steps, in order (each one is its own transaction):
  download  save nflverse files to data/cache/nflverse/ (the raw layer for this source)
            and log each download in raw.nflverse_pull. By default it downloads files that
            are missing plus everything that can still change (the ID crosswalk, the
            schedule, and this season's stats); older seasons come from the saved files.
  xref      core.player_xref: Sleeper player id -> NFL gsis id
  games     core.nfl_game: every NFL game with scores, roof, surface, temperature, wind
  stats     core.nfl_player_week: weekly stat lines for every QB, RB, WR, TE, K and FB,
            plus anyone who was ever on a league roster

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
    python -m etl.load_nflverse --step stats     # one step (repeatable; download still runs first)
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
from etl.match_players import normalize
from etl.nfl_columns import CONTEXT_COLUMNS, GAME_COLUMNS, STAT_COLUMNS

CACHE = PROJECT_ROOT / "data" / "cache" / "nflverse"
OVERRIDES = PROJECT_ROOT / "data" / "manual" / "nfl_id_overrides.csv"
KEEP_POSITIONS = {"QB", "RB", "WR", "TE", "K", "FB"}
FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K"}
SCHEMA_TIP = (
    "Your database is missing a table this code needs. In DBeaver run db/schema/005_nfl_stats.sql,\n"
    "then db/checks/001_validation.sql, then run this command again."
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
    """(dataset, season, file name, how to fetch it). season None = one file for all seasons."""
    plan = [("ff_playerids", None, "ff_playerids.parquet", lambda: nfl.load_ff_playerids()),
            ("schedules", None, "schedules.parquet", lambda: nfl.load_schedules(seasons))]
    for s in seasons:
        plan.append(("player_stats", s, f"player_stats_{s}.parquet",
                     lambda s=s: nfl.load_player_stats([s], summary_level="week")))
    return plan


def download(conn, mode):
    """mode: 'auto' (missing + still-changing files), 'all' (--refresh), 'none' (--no-download)."""
    seasons, current = season_range(conn)
    CACHE.mkdir(parents=True, exist_ok=True)
    problems = []
    for dataset, season, name, fetch in file_plan(seasons):
        path = CACHE / name
        if mode == "none":
            if not path.exists():
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


STEPS = [("xref", load_xref), ("games", load_games), ("stats", load_stats)]


def main():
    parser = argparse.ArgumentParser(description="Load NFL stat lines, games and player ids from nflverse.")
    parser.add_argument("--step", action="append", choices=[n for n, _ in STEPS],
                        help="run only this step (repeatable). Default: all")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh", action="store_true", help="re-download every file first")
    group.add_argument("--no-download", action="store_true", help="use only the saved files")
    args = parser.parse_args()
    mode = "all" if args.refresh else "none" if args.no_download else "auto"

    with get_connection() as conn:
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

        size = conn.execute(
            "select pg_size_pretty(pg_database_size(current_database())), "
            "pg_size_pretty(pg_total_relation_size('core.nfl_player_week'))").fetchone()
        summary = conn.execute("select check_name, problems from qa.summary where problems > 0").fetchall()
    print(f"\nDatabase size: {size[0]} (stat lines: {size[1]}). Neon's free plan allows 1 GB.")
    if summary:
        print("These checks found something to look at (select * from qa.<check name>):")
        for check, count in summary:
            print(f"  {check}: {count}")
    else:
        print("All checks in qa.summary are zero.")


if __name__ == "__main__":
    main()
