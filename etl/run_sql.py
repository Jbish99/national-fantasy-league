"""
etl/run_sql.py

Run the project's SQL files against the database, straight from this folder.
Use this instead of opening the files in DBeaver: DBeaver saves its own copy of a file
when it runs it, which makes `git pull` complain, and it can open an old copy by mistake.
(DBeaver is still the place to look at the data and run your own queries.)

Each file runs in its own transaction: if one fails, nothing from that file is saved
and the run stops there.

Run from the project root:
    python -m etl.run_sql --all                         # every schema file, the checks and the seed, in order
    python -m etl.run_sql db/checks/001_validation.sql  # just the files you name, in that order
    python -m etl.run_sql --reset --all                 # drop core + qa first (raw is kept), then rebuild
"""

import argparse
import sys

import psycopg

from etl.db import PROJECT_ROOT, get_connection

DB = PROJECT_ROOT / "db"
RESET = DB / "reset_core.sql"


def all_files():
    """Every file a full build needs, in the order the README lists them."""
    return sorted((DB / "schema").glob("[0-9][0-9][0-9]_*.sql")) + [
        DB / "checks" / "001_validation.sql",
        DB / "seed" / "001_reference_data.sql",
    ]


def main():
    parser = argparse.ArgumentParser(description="Run project SQL files against the database.")
    parser.add_argument("files", nargs="*", help="SQL files to run, in order (paths from the project root)")
    parser.add_argument("--all", action="store_true", help="run every schema file, then the checks, then the seed")
    parser.add_argument("--reset", action="store_true",
                        help="run db/reset_core.sql first (drops core + qa; raw is kept). Then run the loaders again.")
    args = parser.parse_args()

    files = ([RESET] if args.reset else []) + (all_files() if args.all else []) + \
            [PROJECT_ROOT / f for f in args.files]
    if not files:
        parser.error("name some files, or use --all")
    missing = [f for f in files if not f.exists()]
    if missing:
        print("Can't find: " + ", ".join(str(f.relative_to(PROJECT_ROOT)) for f in missing))
        sys.exit(1)
    if args.reset:
        answer = input("This drops every core and qa table (raw data is kept). Type yes to go on: ")
        if answer.strip().lower() != "yes":
            print("Nothing done.")
            return

    with get_connection() as conn:
        for path in files:
            name = path.relative_to(PROJECT_ROOT).as_posix()
            try:
                conn.execute(path.read_text(encoding="utf-8"))
                conn.commit()
                print(f"  ok   {name}")
            except psycopg.Error as error:
                conn.rollback()
                print(f"  FAILED {name}. Nothing from this file was saved. The database said:\n    {error}")
                sys.exit(1)
        summary = conn.execute("select check_name, problems from qa.summary where problems > 0").fetchall()
    print("\nDone. " + ("Checks with something to look at: " + ", ".join(f"{c} {n}" for c, n in summary)
                        if summary else "All checks in qa.summary are zero."))


if __name__ == "__main__":
    main()
