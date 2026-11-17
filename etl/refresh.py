"""
etl/refresh.py

Phase 6: the in-season refresh. One command runs the whole update, in order:
  1. python -m etl.run_sql --all                    schema, views, checks, app login read access
  2. python -m etl.pull_sleeper --season <current>  this season from Sleeper (players list weekly)
  3. python -m etl.load_workbook                    the All-In-One workbook in data/manual/
  4. python -m etl.load_core                        rebuild the clean tables; finished weeks the
                                                    workbook doesn't have yet come in as provisional
  5. python -m etl.load_nflverse                    this season's NFL stats, games and weather, plus snaps,
                                                    injuries, expected points and play-by-play summaries
  6. python -m etl.precompute                       Phase 11: new data version + rebuild and check the
                                                    precomputed copies of the heavy views (once, here: the
                                                    steps above only stamp the version, NFL_PRECOMPUTE=later)
  7. checks                                         the history checks in qa.summary must pass

GitHub runs this every morning (.github/workflows/refresh.yml). You can also run it yourself
from the project root:
    python -m etl.refresh
    python -m etl.refresh --season 2026      # skip asking Sleeper which season is current

Exit code 0 = all good. Exit code 1 = a step failed or a history check needs a look; on GitHub
that marks the run failed and emails you. NFL-stat and lineup checks only print warnings,
because they can lag a day behind Sleeper (nflverse posts stats after the games).
"""

import argparse
import os
import subprocess
import sys

import requests

from etl.db import PROJECT_ROOT, get_connection

# Checks that only warn (they don't touch the official history), with what each one usually means.
NFL_LAG = "NFL stats can trail Sleeper by a day or two; if it sticks around, ask Claude"
WARN_ONLY = {
    "stub_players": "a player Sleeper's weekly player list doesn't have yet; filled in at the next weekly download",
    "league_players_without_nfl_id": NFL_LAG,
    "nfl_id_shared": NFL_LAG,
    "nfl_stats_without_game": NFL_LAG,
    "scoring_not_covered": "a scoring rule the rebuilt NFL points can't handle; ask Claude",
    "nfl_id_suspect": NFL_LAG,
    "league_defenses_without_stat_line": NFL_LAG,
    "unknown_lineup_slots": "a new lineup slot type (affects the Lineups page only); ask Claude",
    "best_lineup_below_actual": "a player's position looks off (affects the Lineups page only); ask Claude",
    "player_log_duplicates": "a player shows twice in a week on the Player / Box score pages; ask Claude",
    "def_points_accuracy_low": "rebuilt defense points stopped matching Sleeper (DEF weather splits only); ask Claude",
    "trade_vs_acquisitions": "a traded player's points differ between two views (Trade Analyzer only); ask Claude",
    "traded_pick_unmatched": "a traded pick couldn't be tied to the player drafted with it (Trade Analyzer only); ask Claude",
    "replacement_level_mismatch": "trade and draft replacement levels disagree (Trade Analyzer only); ask Claude",
    "points_split_mismatch": "the passing/rushing/receiving split stopped adding up (Team identities only); ask Claude",
    "identity_metric_unknown": "an identity label in db/seed/001 names a measure that doesn't exist (typo?); ask Claude",
    "formation_unknown": "a formation is missing from core.formation in db/seed/001 (deleted row?); ask Claude",
    "precomputed_problem": "a precomputed copy wasn't built or didn't match its view, so the app reads that view "
                           "directly (slower, still correct); ask Claude",
    "week_rank_duplicates": "a player shows twice in the weekly position ranks (rank chips only); ask Claude",
    "week_rank_missing": "a rostered player-week has a stat line but no weekly position rank; usually NFL "
                         "play-by-play trailing for a defense, else ask Claude",
}
# Known, accepted counts. custom_points_used = 2: the 2022 Fantasy Bowl (co-champions).
EXPECTED = {"custom_points_used": 2}

ON_GITHUB = os.getenv("GITHUB_ACTIONS") == "true"


def current_season() -> int:
    league_id = os.getenv("SLEEPER_CURRENT_LEAGUE_ID", "").strip()
    if not league_id:
        sys.exit("SLEEPER_CURRENT_LEAGUE_ID is missing (in .env on your computer, in the workflow file on GitHub).")
    league = requests.get(f"https://api.sleeper.app/v1/league/{league_id}", timeout=30).json()
    if not league or "season" not in league:
        sys.exit(f"Sleeper returned nothing for league {league_id}. Check SLEEPER_CURRENT_LEAGUE_ID.")
    return int(league["season"])


def run(step: str, module: str, *args: str) -> None:
    print(f"\n=== {step}: python -m {module} {' '.join(args)}".rstrip(), flush=True)
    if ON_GITHUB:
        print(f"::group::{step}", flush=True)
    result = subprocess.run([sys.executable, "-m", module, *args], cwd=PROJECT_ROOT)
    if ON_GITHUB:
        print("::endgroup::", flush=True)
    if result.returncode != 0:
        message = f"Refresh stopped at '{step}' (python -m {module} exited with {result.returncode}). " \
                  "Its output above says why."
        annotate("error", message)
        sys.exit(1)


def week_list(weeks) -> str:
    weeks = list(weeks)
    if len(weeks) == 1:
        return f"week {weeks[0]}"
    if weeks == list(range(weeks[0], weeks[-1] + 1)):
        return f"weeks {weeks[0]}-{weeks[-1]}"
    return "weeks " + ", ".join(map(str, weeks))


def annotate(level: str, message: str) -> None:
    """Print a message; on GitHub also pin it to the run's summary page."""
    print(f"::{level}::{message}" if ON_GITHUB else f"{level.upper()}: {message}", flush=True)


def report() -> int:
    """Read qa.summary and the state of the official record. Returns how many history checks failed."""
    with get_connection() as conn:
        checks = conn.execute("select check_name, problems from qa.summary").fetchall()
        latest = conn.execute(
            "select season, max(week) from core.game where season = (select max(season) from core.game) group by season"
        ).fetchone()
        provisional = conn.execute(
            "select season, array_agg(distinct week order by week) from core.game where is_provisional group by season"
        ).fetchall()
        workbook_week = conn.execute(
            "select max(week) from core.game where season = %s and not is_provisional", (latest[0],)
        ).fetchone()[0] if latest else None

    failed = [(c, n) for c, n in checks if c not in WARN_ONLY and n != EXPECTED.get(c, 0)]
    warned = [(c, n) for c, n in checks if c in WARN_ONLY and n]

    print("\n=== Result")
    if latest:
        print(f"Official record goes through {latest[0]} week {latest[1]} "
              f"(workbook through week {workbook_week}).")
    for season, weeks in provisional:
        print(f"Provisional (from Sleeper, waiting on the workbook): {season} {week_list(weeks)}")
    for name, count in failed:
        annotate("error", f"History check {name} = {count} (expected {EXPECTED.get(name, 0)}). "
                          f"Look at it in DBeaver:  select * from qa.{name};")
    for name, count in warned:
        annotate("warning", f"{name} = {count}: {WARN_ONLY[name]}. Details:  select * from qa.{name};")
    if not failed:
        print("History checks: all good." + (" (Some warnings above.)" if warned else ""))

    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:                                    # the box at the top of the run's page on GitHub
        lines = ["## League data refresh", ""]
        if latest:
            lines.append(f"- Official record through **{latest[0]} week {latest[1]}** (workbook through week {workbook_week})")
        for season, weeks in provisional:
            lines.append(f"- Provisional from Sleeper (waiting on the workbook): {season} {week_list(weeks)}")
        lines.append("- History checks: " + ("**all good**" if not failed else
                     "**need a look**: " + ", ".join(f"`{c}` {n}" for c, n in failed)))
        if warned:
            lines.append("- Warnings: " + ", ".join(f"`{c}` {n}" for c, n in warned))
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return len(failed)


def main():
    parser = argparse.ArgumentParser(description="Refresh the league database: Sleeper, workbook, core, NFL stats.")
    parser.add_argument("--season", type=int, help="season to pull from Sleeper (default: the current league's)")
    args = parser.parse_args()

    season = args.season or current_season()
    print(f"Refreshing the {season} season.")
    os.environ["NFL_PRECOMPUTE"] = "later"   # the steps below only stamp the data version; copies are built once, last
    run("Build/update the database", "etl.run_sql", "--all")
    run("Pull from Sleeper", "etl.pull_sleeper", "--season", str(season))
    run("Load the workbook", "etl.load_workbook")
    run("Rebuild the clean tables", "etl.load_core")
    run("NFL stats", "etl.load_nflverse")
    run("Precomputed copies", "etl.precompute")
    if report():
        sys.exit(1)


if __name__ == "__main__":
    main()
