"""
etl/compare_dashboards.py

History check: compare the All-In-One workbook's Scores Dashboard with the database.

The database's official record (core.game) uses the workbook's games, but takes Sleeper's
scores in Sleeper-scored seasons (they include late stat corrections) and records the 2022
Fantasy Bowl as a tie (co-champions). The workbook's dashboard uses its own typed scores.
So for every difference this script also re-scores the history the workbook's way; if
that reproduces the dashboard, the difference is EXPLAINED (it's the policy, not an error)
and the games behind it are listed. Anything else is UNEXPLAINED and worth a look.

Compared: head-to-head matrix, most/least points in a season, largest/smallest margins,
highest/lowest scores, highest/lowest combined scores, Game of the Week, International,
IST, division and conference records.

Run from the project root (after load_core):
    python -m etl.compare_dashboards
    python -m etl.compare_dashboards --details      # also list every explained difference
Exit code is 1 if anything is unexplained.
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import openpyxl

from etl.common import franchise_code_for
from etl.db import PROJECT_ROOT, get_connection

DEFAULT_FILE = PROJECT_ROOT / "data" / "manual" / "All-In-One.xlsx"
SHEET = "Scores Dashboard"
NOW = 2026      # season used to read team names that have no year next to them ("Up North N")


# ---------------------------------------------------------------------------
# The games, scored two ways
# ---------------------------------------------------------------------------

@dataclass
class Game:
    game_id: str
    season: int
    week: int
    game_type: str
    designation: str | None
    teams: tuple                       # (team1_code, team2_code)
    official: dict                     # code -> points (None if unknown)
    winner: str | None                 # official winner (None = tie / co-champions)
    sheet: dict                        # code -> points as typed in the workbook
    note: str | None
    international: bool
    gotw: bool
    division: bool
    conference: bool

    def points(self, code, model):
        return (self.official if model == "official" else self.sheet).get(code)

    def result(self, code, model):
        if model == "sheet":
            a, b = self.sheet.get(code), self.sheet.get(self.other(code))
            if a is not None and b is not None:
                return "W" if a > b else "L" if a < b else "T"
        return "T" if self.winner is None else "W" if self.winner == code else "L"

    def other(self, code):
        return self.teams[1] if code == self.teams[0] else self.teams[0]

    def label(self):
        return f"{self.season} W{self.week} {self.teams[0]}-{self.teams[1]}" + (
            f" ({self.designation})" if self.designation else "")

    def why_different(self, only=None):
        """Plain-English reason the official and workbook versions of this game differ, or None.
        only: a team code, to explain just that team's side (its score and result)."""
        reasons = []
        a, _ = self.teams
        if self.result(a, "official") != self.result(a, "sheet"):
            official = "tie" if self.winner is None else f"{self.winner} win"
            sheet = self.result(a, "sheet")
            sheet = "a tie" if sheet == "T" else f"{a if sheet == 'W' else self.other(a)} win"
            reasons.append(f"official result is a {official}; the workbook's scores make it {sheet}")
        for code in self.teams if only is None else (only,):
            o, s = self.official.get(code), self.sheet.get(code)
            if o is None and s is not None:
                reasons.append(f"{code} has no official score (only the winner is known); "
                               f"the workbook has a placeholder {s:g}")
            elif o is not None and s is not None and abs(o - s) >= 0.005:
                reasons.append(f"{code} {o:.2f} official (Sleeper, stat-corrected) vs {s:.2f} in the workbook")
        return "; ".join(reasons) or None


def load_games(conn):
    sheet = {}
    for (d,) in conn.execute("select data from raw.workbook_row where sheet = 'Scores'"):
        season, week = int(d["Year"]), int(d["Week"])
        c1, c2 = franchise_code_for(d["Team 1"], season), franchise_code_for(d["Team 2"], season)
        sheet[(season, week, frozenset((c1, c2)))] = {c1: num(d.get("Team 1 Score")), c2: num(d.get("Team 2 Score"))}
    games = []
    for row in conn.execute(
            """
            select game_id, season, week, game_type, designation, team1_code, team2_code, team1_points,
                   team2_points, winner_code, result_note, international_location is not null,
                   is_game_of_week, is_division_game, is_conference_game
            from core.game order by season, week, game_id
            """):
        (gid, season, week, gtype, desig, t1, t2, p1, p2, winner, note, intl, gotw, div, conf) = row
        games.append(Game(gid, season, week, gtype, desig, (t1, t2), {t1: num(p1), t2: num(p2)}, winner,
                          sheet.get((season, week, frozenset((t1, t2))), {}), note, intl, gotw, div, conf))
    return games


def num(value):
    try:
        return None if value in (None, "") else round(float(value), 3)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Reading the dashboard
# ---------------------------------------------------------------------------

def find_table(ws, title):
    """Rows of the table under a title cell, as dicts keyed by the header row."""
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.strip() == title:
                return read_table(ws, cell.row + 1, cell.column)
    return None


def read_table(ws, header_row, first_col):
    headers, col = [], first_col
    while True:
        value = ws.cell(header_row, col).value
        if value in (None, "") or value in headers:
            break
        headers.append(str(value).strip())
        col += 1
    rows, r = [], header_row + 1
    while ws.cell(r, first_col).value not in (None, ""):
        rows.append({h: ws.cell(r, first_col + i).value for i, h in enumerate(headers)})
        r += 1
    return rows


def record(value):
    """'6-5' -> (6, 5, 0); '6-5-1' -> (6, 5, 1). Excel sometimes turns '3-4' into March 4."""
    if isinstance(value, (datetime, date)):
        return (value.month, value.day, 0)
    parts = str(value or "").strip().split("-")
    if len(parts) in (2, 3) and all(p.strip().isdigit() for p in parts):
        nums = [int(p) for p in parts]
        return (nums[0], nums[1], nums[2] if len(nums) == 3 else 0)
    return None


def code(name, season=NOW):
    return franchise_code_for(name, int(season)) if name else None


def year_week(value):
    """'2025 W5' -> (2025, 5)"""
    year, _, week = str(value).partition(" W")
    return int(year), int(week)


def same(a, b):
    return a is not None and b is not None and abs(float(a) - float(b)) < 0.005


# ---------------------------------------------------------------------------
# Comparisons. Each returns (checked, matched, explained [(what, why)], unexplained [(what, why)])
# ---------------------------------------------------------------------------

class Tally:
    def __init__(self, name):
        self.name, self.checked, self.matched = name, 0, 0
        self.explained, self.unexplained = [], []

    def add(self, what, dashboard, official, sheet, games=(), team=None):
        """One value: the dashboard's, ours (official), and ours scored the workbook's way."""
        self.checked += 1
        if dashboard == official:
            self.matched += 1
            return
        reasons = sorted({f"{g.label()}: {g.why_different(team)}" for g in games if g.why_different(team)})
        if dashboard == sheet and reasons:
            self.explained.append((what, f"dashboard {fmt(dashboard)}, database {fmt(official)}", reasons))
        else:
            self.unexplained.append((what, f"dashboard {fmt(dashboard)}, database {fmt(official)}", reasons))


def fmt(value):
    if isinstance(value, tuple):
        return "-".join(str(round(v, 2)) if isinstance(v, float) else str(v) for v in value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def r2(value):
    return None if value is None else round(value, 2)


def compare_h2h(ws, games):
    t = Tally("Head-to-head matrix (all games)")
    rows = find_table(ws, "HEAD-TO-HEAD MATRIX")
    if rows is None:
        return None
    by_pair = defaultdict(list)
    for g in games:
        for me in g.teams:
            by_pair[(me, g.other(me))].append(g)
    for row in rows:
        me = code(row["Team"])
        for opp_name, value in row.items():
            if opp_name == "Team" or record(value) is None:
                continue
            opp = code(opp_name)
            played = by_pair.get((me, opp), [])
            counts = {m: tuple(sum(1 for g in played if g.result(me, m) == r) for r in "WLT")
                      for m in ("official", "sheet")}
            t.add(f"{row['Team']} vs {opp_name}", record(value), counts["official"], counts["sheet"], played, me)
    return t


def compare_seasons(ws, games, title):
    t = Tally(title.capitalize().replace("points in a season", "points in a season (regular season)"))
    rows = find_table(ws, title)
    if rows is None:
        return None
    for row in rows:
        season, me = int(row["Year"]), code(row["Team"], row["Year"])
        mine = [g for g in games if g.season == season and g.game_type == "regular" and me in g.teams]
        values = {m: (r2(sum(g.points(me, m) or 0 for g in mine)), len(mine)) for m in ("official", "sheet")}
        t.add(f"{row['Team']} {season}", (r2(num(row["Points"])), int(row["Games"])),
              values["official"], values["sheet"], mine, me)
    return t


def find_game(games, season, week, a, b=None):
    for g in games:
        if g.season == season and g.week == week and a in g.teams and (b is None or b in g.teams):
            return g
    return None


def compare_game_list(ws, games, title, kind):
    t = Tally(title.capitalize())
    rows = find_table(ws, title)
    if rows is None:
        return None
    for row in rows:
        if kind == "score":
            season, week = year_week(row["Year/Week"])
            me = code(row["Team"], season)
            g = find_game(games, season, week, me, code(row["Opponent"], season))
            dash = r2(num(row["Score"]))
            values = {m: r2(g.points(me, m)) if g else None for m in ("official", "sheet")}
            what = f"{season} W{week} {row['Team']}"
        else:
            season, week = int(row["Year"]), int(row["Week"])
            g = find_game(games, season, week, code(row["Winner"], season), code(row["Loser"], season))
            column = "Margin" if kind == "margin" else "Combined"
            dash = r2(num(row[column]))
            values = {}
            for m in ("official", "sheet"):
                a, b = (g.points(x, m) for x in g.teams) if g else (None, None)
                values[m] = None if a is None or b is None else r2(abs(a - b) if kind == "margin" else a + b)
            what = f"{season} W{week} {row['Winner']} vs {row['Loser']}"
        if g is None:
            t.checked += 1
            t.unexplained.append((what, "this game isn't in the database", []))
            continue
        t.add(what, dash, values["official"], values["sheet"], [g])
    return t


RECORD_TABLES = [
    ("GAME OF THE WEEK", "Game of the Week records", lambda g: g.gotw),
    ("INTERNATIONAL GAME RECORDS", "International game records", lambda g: g.international),
    ("IST GAME RECORDS", "In-Season Tournament (IST) records", lambda g: g.designation == "IST Game"),
    ("DIVISION", "Division game records", lambda g: g.division),
    ("CONFERENCE", "Conference game records", lambda g: g.conference),
]


def compare_records(ws, games, title, name, keep):
    t = Tally(name)
    rows = find_table(ws, title)
    if rows is None:
        return None
    for row in rows:
        me = code(row["Team"])
        mine = [g for g in games if keep(g) and me in g.teams]
        values = {}
        for m in ("official", "sheet"):
            results = [g.result(me, m) for g in mine]
            values[m] = (len(mine), results.count("W"), results.count("L"), results.count("T"),
                         r2(sum(g.points(me, m) or 0 for g in mine)))
        gp = row.get("GP", row.get("Appearances"))
        dash = (int(gp or 0), int(row["W"] or 0), int(row["L"] or 0), int(row["T"] or 0), r2(num(row["PF"])))
        t.add(f"{row['Team']} (GP-W-L-T-PF)", dash, values["official"], values["sheet"], mine, me)
    return t


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Compare the workbook's Scores Dashboard with the database.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="path to All-In-One.xlsx")
    parser.add_argument("--details", action="store_true", help="also list every explained difference")
    args = parser.parse_args()
    if not args.file.exists():
        print(f"Can't find {args.file}. Put the workbook in data/manual/ or pass --file.")
        sys.exit(1)

    wb = openpyxl.load_workbook(args.file, data_only=True, read_only=False)
    if SHEET not in wb.sheetnames:
        print(f"The workbook has no '{SHEET}' sheet.")
        sys.exit(1)
    ws = wb[SHEET]
    h2h_filter = str(ws["B5"].value or "").strip()

    with get_connection() as conn:
        games = load_games(conn)

    tallies = [compare_h2h(ws, games) if h2h_filter in ("", "All Games") else None]
    tallies += [compare_seasons(ws, games, t) for t in ("MOST POINTS IN A SEASON", "LEAST POINTS IN A SEASON")]
    tallies += [compare_game_list(ws, games, t, k) for t, k in (
        ("TOP 20 LARGEST MARGINS", "margin"), ("TOP 20 SMALLEST MARGINS", "margin"),
        ("TOP 20 HIGHEST SCORES", "score"), ("TOP 20 LOWEST SCORES", "score"),
        ("TOP 20 HIGHEST COMBINED", "combined"), ("TOP 20 LOWEST COMBINED", "combined"))]
    tallies += [compare_records(ws, games, *spec) for spec in RECORD_TABLES]

    print(f"Workbook: {args.file.name}  vs  database ({len(games)} official games)\n")
    if h2h_filter not in ("", "All Games"):
        print(f"(Skipped the head-to-head matrix: its filter is set to '{h2h_filter}', not 'All Games'.)\n")
    unexplained = explained_count = 0
    for t in tallies:
        if t is None:
            continue
        status = "OK" if not t.unexplained else "CHECK"
        extra = f", {len(t.explained)} explained" if t.explained else ""
        print(f"[{status:5}] {t.name}: {t.matched} of {t.checked} match{extra}"
              + (f", {len(t.unexplained)} UNEXPLAINED" if t.unexplained else ""))
        for what, values, reasons in t.unexplained:
            print(f"          X {what}: {values}")
            for reason in reasons:
                print(f"              {reason}")
        if args.details:
            for what, values, reasons in t.explained:
                print(f"          ~ {what}: {values}")
                for reason in reasons:
                    print(f"              because {reason}")
        unexplained += len(t.unexplained)
        explained_count += len(t.explained)

    print()
    if unexplained:
        print(f"{unexplained} unexplained difference(s). Those are worth a look (listed above).")
    else:
        print("Everything matches, or differs only because of the database's score policy"
              + (f" ({explained_count} explained difference(s); add --details to list them)." if explained_count else "."))
    sys.exit(1 if unexplained else 0)


if __name__ == "__main__":
    main()
