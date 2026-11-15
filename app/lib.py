"""
app/lib.py

Shared helpers for the Streamlit app: the database connection, cached queries,
and small formatting functions. Every page imports from here.

Where the database address comes from:
  - On Streamlit Community Cloud (Phase 5): the DATABASE_URL secret.
  - On your computer: DATABASE_URL in the project's .env file (the pooled one, made for apps).
"""

import os
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg.types.numeric import FloatLoader

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Chart colors (from the validated reference palette)
SERIES_BLUE = "#2a78d6"      # the one data series
CONTEXT_GRAY = "#8a8985"     # league average / context lines
TEXT_SECONDARY = "#52514e"
# Head-to-head diverging scale: losing record (red tint) <- .500 (neutral gray) -> winning record (blue tint).
# Light tints so the record printed in each cell stays readable.
DIVERGING = ["#f1a4a3", "#f0efec", "#86b6ef"]
ROW_PX = 35                  # height of one table row in st.dataframe


def _database_url():
    try:
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:                      # no .streamlit/secrets.toml on this computer: that's fine
        pass
    load_dotenv(PROJECT_ROOT / ".env")
    return os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_UNPOOLED")


@st.cache_data(ttl=600, show_spinner="Loading league history...")
def query(sql, params=None):
    """Run a read-only query and return a DataFrame. Results are cached for 10 minutes."""
    url = _database_url()
    if not url:
        st.error("No database address found. Add DATABASE_URL to the .env file (or to Streamlit's secrets).")
        st.stop()
    try:
        with psycopg.connect(url) as conn:
            conn.adapters.register_loader("numeric", FloatLoader)   # numbers come back as floats, not Decimals
            cur = conn.execute(sql, params)
            columns = [c.name for c in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=columns)
    except psycopg.OperationalError as error:
        st.error(f"Couldn't reach the database: {str(error).splitlines()[0]}")
        st.stop()


# ---------------------------------------------------------------------------
# Shared lookups
# ---------------------------------------------------------------------------

@st.cache_resource(ttl=600)
def franchises():
    """franchise_code -> (full name, short name). Short names match the workbook ('Up North N').
    Kept as one shared object (cache_resource), so looking names up is instant."""
    df = query("select franchise_code, location, nickname from core.franchise order by location, nickname")
    counts = df["location"].value_counts()
    out = {}
    for code, location, nickname in df.itertuples(index=False):
        short = f"{location} {nickname[0]}" if counts[location] > 1 else location
        out[code] = (f"{location} {nickname}", short)
    return out


def full_name(code):
    return franchises().get(code, (code, code))[0]


def short_name(code):
    return franchises().get(code, (code, code))[1]


@st.cache_data(ttl=600, show_spinner=False)
def team_games():
    """Every official game from each team's side (core.v_team_game), with names added."""
    df = query(
        """
        select game_id, season, week, game_type, designation, franchise_code, team_name, opponent_code,
               points_for, points_against, result, margin, is_game_of_week, international_location
        from core.v_team_game
        order by season, week
        """
    )
    names = {code: pair[0] for code, pair in franchises().items()}
    df["franchise"] = df["franchise_code"].map(names).fillna(df["franchise_code"])
    df["opponent"] = df["opponent_code"].map(names).fillna(df["opponent_code"])
    return df


def seasons():
    return query("select season, platform, status, last_scored_week from core.season order by season")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def record(wins, losses, ties=0):
    wins, losses, ties = int(wins or 0), int(losses or 0), int(ties or 0)
    return f"{wins}-{losses}-{ties}" if ties else f"{wins}-{losses}"


def text(value):
    """A value for display: '' instead of None / NaN."""
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def ordinal(n):
    if n is None or pd.isna(n):
        return ""
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def game_label(row):
    """'Fantasy Bowl', 'Playoffs', 'Week 7'... for a game row."""
    designation = row["designation"]
    if isinstance(designation, str) and designation and designation != "IST Game":
        return designation
    return f"Week {row['week']}"


def table(data, container=None, **kwargs):
    """st.dataframe sized to show every row (no hidden scrollbar), without the index column."""
    kwargs.setdefault("hide_index", True)
    kwargs.setdefault("height", ROW_PX * (len(data) + 1) + 3)
    (container or st).dataframe(data, **kwargs)


def provisional_weeks():
    """{season: [weeks]} whose games came straight from Sleeper because the workbook doesn't have them yet."""
    try:
        df = query("select distinct season, week from core.game where is_provisional order by season, week")
    except psycopg.Error:                  # database not updated yet (db/schema/011 not run): no note, no crash
        return {}
    weeks = {}
    for r in df.itertuples():
        weeks.setdefault(int(r.season), []).append(int(r.week))
    return weeks


def week_list(weeks):
    """[10, 11] -> 'weeks 10-11'; [10] -> 'week 10'; [10, 12] -> 'weeks 10, 12'."""
    if len(weeks) == 1:
        return f"week {weeks[0]}"
    if weeks == list(range(weeks[0], weeks[-1] + 1)):
        return f"weeks {weeks[0]}-{weeks[-1]}"
    return "weeks " + ", ".join(str(w) for w in weeks)


def footer():
    st.caption("Official results come from the league's All-In-One workbook; scores from 2021 on use Sleeper's "
               "stat-corrected numbers except 2023 (workbook). 2022 Fantasy Bowl: co-champions.")
    for season, weeks in provisional_weeks().items():
        st.caption(f"{season} {week_list(weeks)} {'is' if len(weeks) == 1 else 'are'} provisional, straight from "
                   "Sleeper. Once the workbook is updated, its version replaces these games "
                   "(adding Game of the Week and division tags).")
