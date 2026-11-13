"""
etl/common.py

Small helpers shared by the loaders (load_core, match_players, and the Phase 3 loaders):
reading the raw layer, turning workbook team names into franchise codes, and the
LoadProblem error that stops a step with a plain-English list of problems.
"""

import re
from datetime import datetime, timezone

WORKBOOK = "All-In-One.xlsx"


class LoadProblem(Exception):
    """Raised with a list of plain-English problems; the step is rolled back."""

    def __init__(self, step, problems):
        self.step = step
        self.problems = problems
        super().__init__(f"{step}: {len(problems)} problem(s)")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def latest(conn, endpoint):
    """Newest copy of every Sleeper response for an endpoint: [(season, week, payload)]."""
    return conn.execute(
        "select season, week, payload from raw.v_sleeper_latest "
        "where endpoint = %s order by season, week nulls first",
        (endpoint,),
    ).fetchall()


def workbook_rows(conn, sheet):
    rows = conn.execute(
        "select row_number, data from raw.workbook_row "
        "where workbook = %s and sheet = %s order by row_number",
        (WORKBOOK, sheet),
    ).fetchall()
    if not rows:
        raise LoadProblem(sheet, [f"No '{sheet}' rows in raw.workbook_row. Run: python -m etl.load_workbook"])
    return rows


def roster_map(conn):
    """(season, sleeper_roster_id) -> franchise_code"""
    return {
        (season, rid): code
        for season, rid, code in conn.execute(
            "select season, sleeper_roster_id, franchise_code from core.franchise_season "
            "where sleeper_roster_id is not null"
        )
    }


def season_points(settings, key):
    """Sleeper stores season points as a whole part plus hundredths, e.g. fpts + fpts_decimal."""
    whole = settings.get(key)
    if whole is None:
        return None
    return round(whole + (settings.get(f"{key}_decimal") or 0) / 100, 2)


def from_millis(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms else None


def to_number(value):
    if value is None or value == "":
        return None
    return round(float(value), 2)


def to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "y", "1")


def winner_of(code1, code2, pts1, pts2):
    if pts1 is None or pts2 is None or pts1 == pts2:
        return None
    return code1 if pts1 > pts2 else code2


def is_empty_slot(player_id):
    return player_id in (None, "", "0")


# ---------------------------------------------------------------------------
# Team names in the workbook -> franchise codes
# ---------------------------------------------------------------------------

LOCATIONS = {  # longest names are tried first, so "Oak Park" never matches "Oakland"
    "vehicle city": "VHC", "grand rapids": "GRR", "grand blanc": "GRB", "great lakes": "GRL",
    "sand point": "SDP", "stonebrook": "STO", "mississippi": "MIS", "farmington": "FAR",
    "oak park": "OAP", "oakland": "OAK", "detroit": "DET", "montana": "MON", "flint": "FLT",
}


def franchise_code_for(name, season):
    """Turn any spelling the workbook uses into a franchise code, or None if unknown."""
    if not name:
        return None
    text = re.sub(r"\s+", " ", str(name)).strip().lower()
    if text.startswith("up north"):
        rest = text[len("up north"):].strip(" ()")
        if rest.startswith("b"):          # "Up North B", "Up North (Blizz)", "Up North Blizzard"
            return "UPB"
        if rest.startswith("n"):          # "Up North N", "Up North Nightmare"
            return "UPN"
        return "UPB" if season <= 2020 else "UPN"
    for location in sorted(LOCATIONS, key=len, reverse=True):
        if text.startswith(location):
            return LOCATIONS[location]
    return None


def clean_name(name):
    """A workbook name with extra spaces squeezed out ('  Josh  Allen ' -> 'Josh Allen')."""
    if name is None:
        return None
    text = re.sub(r"\s+", " ", str(name)).strip()
    return text or None
