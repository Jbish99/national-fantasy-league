"""
etl/precompute.py  (Phase 11: speed-up)

Two things the app's speed depends on, both kept honest here:
  1. The data version (core.data_version): a number that goes up whenever core data or a view changes. The app
     checks it about once a minute and keeps every result cached until it changes, so new data shows right after
     a load and nobody waits for the same queries twice.
  2. Precomputed copies of the slowest views (the list is core.precomputed, set in db/schema/018_speed.sql): each
     view is saved as a plain table, core.copy_<name>, then checked row for row against the view. The app uses a
     copy only when it passed that check for the current data version; otherwise it reads the view itself. So a
     stale or broken copy can't show wrong history; at worst a page is slower.

load_core, load_nflverse and run_sql call this on their own (data_changing() at the start, data_changed() at the
end). The daily refresh sets NFL_PRECOMPUTE=later so the copies are built once, as its last step.

Run it yourself (from the project root) after changing data by hand in DBeaver or the Neon SQL editor:
    python -m etl.precompute            new data version + rebuild and check every copy
    python -m etl.precompute --status   list the copies and whether the app is using them
"""

import argparse
import os
import sys
import time

import psycopg

from etl.db import get_connection

LOCK_ID = 1102026          # pg advisory lock: two rebuilds never run at once


def _ready(conn) -> bool:
    """True once db/schema/018_speed.sql has been applied."""
    row = conn.execute("select to_regclass('core.data_version') is not null "
                       "and to_regclass('core.precomputed') is not null").fetchone()
    return bool(row[0])


def bump(conn, by: str) -> int | None:
    """New data version. The app drops its cached results and stops using copies made for the old version."""
    if not _ready(conn):
        return None
    version = conn.execute("update core.data_version set version = version + 1, stamped_at = now(), "
                           "stamped_by = %s returning version", (by,)).fetchone()[0]
    conn.commit()
    return version


def _split(name: str) -> tuple[str, str]:
    schema, _, table = name.partition(".")
    return schema, table


def _ident(name: str) -> str:
    """'core.copy_x' -> '"core"."copy_x"' (names come from core.precomputed, still quoted to be safe)."""
    return ".".join(f'"{part}"' for part in _split(name))


def _record(conn, view, **fields):
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    conn.execute(f"update core.precomputed set {sets} where view_name = %(view)s", {**fields, "view": view})


def build_one(conn, view: str, copy: str, index_columns: list[str], version: int, app_reader: bool) -> str:
    """Build one copy, check it equals its view, swap it in. Returns a one-line result."""
    schema, table = _split(copy)
    new_table = f"{table}__new"
    new = f"{schema}.{new_table}"
    start = time.perf_counter()
    if conn.execute("select to_regclass(%s)", (view,)).fetchone()[0] is None:
        conn.execute(f"drop table if exists {_ident(copy)}")
        _record(conn, view, status="view missing", data_version=None, row_count=None)
        conn.commit()
        return f"{view}: the view doesn't exist (copy removed)"
    try:
        # Same plan for building and checking: parallel workers can add up decimals in a different order, which
        # would make the row-for-row check fail on the last digit of a float.
        conn.execute("set local max_parallel_workers_per_gather = 0")
        conn.execute(f"drop table if exists {_ident(new)}")
        # A regular (logged) table on purpose: Neon empties UNLOGGED tables whenever the database sleeps.
        conn.execute(f"create table {_ident(new)} as select * from {_ident(view)}")
        rows = conn.execute(f"select count(*) from {_ident(new)}").fetchone()[0]
        differing = conn.execute("select core.copy_differences(%s::regclass, %s::regclass)", (view, new)).fetchone()[0]
        if differing:
            conn.rollback()
            _record(conn, view, status=f"differs from its view ({differing} rows)", data_version=None)
            conn.commit()
            return f"{view}: NOT USED, {differing} rows differ from the view right after building it"
        for i, columns in enumerate(index_columns, 1):
            conn.execute(f'create index "{new_table}_i{i}" on {_ident(new)} ({columns})')
        # The swap: the old copy is locked only from here to the commit (a moment).
        conn.execute(f"drop table if exists {_ident(copy)}")
        conn.execute(f'alter table {_ident(new)} rename to "{table}"')
        for i in range(1, len(index_columns) + 1):
            conn.execute(f'alter index "{schema}"."{new_table}_i{i}" rename to "{table}_i{i}"')
        conn.execute(f"comment on table {_ident(copy)} is "
                     f"'Phase 11: precomputed copy of {view} (rebuilt by etl/precompute.py; the view is the source "
                     f"of truth). Data version {version}.'")
        if app_reader:
            conn.execute(f"grant select on {_ident(copy)} to app_reader")
        conn.execute(f"analyze {_ident(copy)}")
        seconds = round(time.perf_counter() - start, 2)
        _record(conn, view, status="ok", data_version=version, row_count=rows, built_at="now", build_seconds=seconds)
        conn.commit()
        return f"{copy}: {rows:,} rows, matches its view ({seconds:.1f} s)"
    except psycopg.Error as error:
        conn.rollback()
        reason = str(error).strip().splitlines()[0][:200]
        _record(conn, view, status=f"failed: {reason}", data_version=None)
        conn.commit()
        return f"{view}: FAILED ({reason})"


def rebuild(conn, quiet: bool = False) -> list[str]:
    """Rebuild and check every copy for the current data version. Problems are recorded in core.precomputed
    (qa.precomputed_problem) instead of stopping the run: the app then reads those views directly."""
    if not _ready(conn):
        return ["precomputed copies: skipped (run `python -m etl.run_sql --all` first)"]
    conn.execute("select pg_advisory_lock(%s)", (LOCK_ID,))
    conn.commit()
    try:
        version = conn.execute("select version from core.data_version").fetchone()[0]
        app_reader = conn.execute("select exists (select 1 from pg_roles where rolname = 'app_reader')").fetchone()[0]
        todo = conn.execute("select view_name, copy_name, index_columns from core.precomputed "
                            "order by sort_order, view_name").fetchall()
        conn.commit()
        results = []
        for view, copy, index_columns in todo:
            line = build_one(conn, view, copy, list(index_columns or []), version, app_reader)
            results.append(line)
            if not quiet:
                print(f"    {line}", flush=True)
        # copies whose row was taken out of the list
        listed = {copy.split(".", 1)[1] for _, copy, _ in todo}
        stray = [r[0] for r in conn.execute(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'core' and c.relkind = 'r' and c.relname like 'copy\\_%%'").fetchall()
            if r[0] not in listed]
        for name in stray:
            conn.execute(f'drop table if exists "core"."{name}"')
            results.append(f"core.{name}: removed (no longer in core.precomputed)")
        conn.commit()
        return results
    finally:
        conn.execute("select pg_advisory_unlock(%s)", (LOCK_ID,))
        conn.commit()


def data_changing(conn, by: str) -> None:
    """Call before a load changes core data: the app stops using the copies and old cached results right away."""
    try:
        bump(conn, f"{by} (started)")
    except psycopg.Error as error:          # never let the bookkeeping stop a load
        conn.rollback()
        print(f"  (couldn't stamp a new data version: {str(error).splitlines()[0]})")


def data_changed(conn, by: str) -> None:
    """Call after a load (or run_sql) finished: new data version, then rebuild the copies (unless the daily
    refresh asked to do that once at the end: NFL_PRECOMPUTE=later)."""
    try:
        version = bump(conn, by)
        if version is None:
            return
        if os.getenv("NFL_PRECOMPUTE", "").strip().lower() == "later":
            print(f"Data version {version}. Precomputed copies: rebuilt at the end of the refresh.")
            return
        print(f"Data version {version}. Rebuilding the precomputed copies (the app reads the views until then)...",
              flush=True)
        results = rebuild(conn, quiet=True)
        problems = [r for r in results if "NOT USED" in r or "FAILED" in r]
        print(f"  {len(results) - len(problems)} of {len(results)} copies ready"
              + ("" if not problems else ". Problems (the app reads those views directly):"))
        for line in problems:
            print(f"    {line}")
    except psycopg.Error as error:
        conn.rollback()
        print(f"  (precomputed copies not rebuilt: {str(error).splitlines()[0]}; the app reads the views directly. "
              "Run `python -m etl.precompute` to try again.)")


def status(conn) -> None:
    if not _ready(conn):
        print("Not set up yet: run `python -m etl.run_sql --all`.")
        return
    version, stamped_at, by = conn.execute("select version, stamped_at, stamped_by from core.data_version").fetchone()
    print(f"Data version {version}, stamped {stamped_at:%Y-%m-%d %H:%M} by {by}.")
    print(f"{'view':34} {'copy':34} {'in use':>6} {'rows':>8} {'built':>17}  status")
    for view, copy, in_use, rows, built, st in conn.execute(
            "select view_name, copy_name, status = 'ok' and data_version = %s, row_count, built_at, status "
            "from core.precomputed order by sort_order", (version,)):
        built_text = f"{built:%Y-%m-%d %H:%M}" if built else "-"
        print(f"{view:34} {copy:34} {'yes' if in_use else 'NO':>6} {rows if rows is not None else '-':>8} "
              f"{built_text:>17}  {st}")


def main():
    parser = argparse.ArgumentParser(description="New data version + rebuild the precomputed copies.")
    parser.add_argument("--status", action="store_true", help="only list the copies and whether the app uses them")
    args = parser.parse_args()
    with get_connection() as conn:
        if args.status:
            status(conn)
            return
        if not _ready(conn):
            sys.exit("core.data_version isn't there yet: run `python -m etl.run_sql --all` first.")
        version = bump(conn, "precompute")
        print(f"Data version {version}. Rebuilding and checking the precomputed copies...", flush=True)
        start = time.perf_counter()
        results = rebuild(conn)
        problems = [r for r in results if "NOT USED" in r or "FAILED" in r]
        print(f"Done in {time.perf_counter() - start:.0f} s: {len(results) - len(problems)} of {len(results)} "
              "copies in use." + (" The others are read straight from their views (slower, still correct); "
                                  "see qa.precomputed_problem." if problems else ""))


if __name__ == "__main__":
    main()
