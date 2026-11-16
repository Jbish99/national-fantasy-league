"""
app/lib.py

Shared helpers for the Streamlit app: the database connection, cached queries, small formatting
functions, and (Phase 8) the league look: the CSS, team art and the shared cards every page uses.
Every page imports from here.

Where the database address comes from:
  - On Streamlit Community Cloud (Phase 5): the DATABASE_URL secret.
  - On your computer: DATABASE_URL in the project's .env file (the pooled one, made for apps).

League art: app/static/league/assets.csv (written by etl/import_league_assets.py) lists every image.
Streamlit serves app/static/ at the address app/static/... (enableStaticServing in .streamlit/config.toml).
"""

import base64
import csv
import html as _html
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg.types.numeric import FloatLoader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_CSV = PROJECT_ROOT / "app" / "static" / "league" / "assets.csv"
ART_URL = "app/static/league/"

# ---------------------------------------------------------------------------
# Colors (dark theme). Page colors match .streamlit/config.toml.
# ---------------------------------------------------------------------------
BG = "#0E1421"               # page
CARD = "#172033"             # cards
LINE = "#26314A"             # borders, grid lines
TEXT = "#F2F5FA"
TEXT_SECONDARY = "#AEB8C9"
MUTED = "#8592A8"            # small labels, axis text
ACCENT = "#F3C54A"           # gold highlight (also config primaryColor)
# Chart colors: dark-mode steps of the validated reference palette (checked against BG and CARD).
SERIES_BLUE = "#3987e5"      # the one data series
CONTEXT_GRAY = "#8592A8"     # league average / context lines
NEG_RED = "#e66767"
NEUTRAL_MID = "#2B3447"      # diverging midpoint: "even" reads as no color on the dark page
# Diverging scale: below (red) <- even (dark gray) -> above (blue). Used with interpolate="rgb".
DIVERGING = [NEG_RED, NEUTRAL_MID, SERIES_BLUE]
ROW_PX = 35                  # height of one table row in st.dataframe
NO_COLORS = ("#2A3550", "#5D6B86")   # badge colors for a franchise with none on file (MON, UPB, MIS)


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


def city(code):
    """'Up North' for both Up North teams (the logo tells them apart), else the short name."""
    name = short_name(code)
    return name[:-2] if name.endswith((" B", " N")) and name.startswith("Up North") else name


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


def pts(value, season=None):
    """A score for display: '' when unknown; whole numbers for 2020 (ESPN scored in whole points)."""
    if value is None or pd.isna(value):
        return ""
    return f"{value:.0f}" if season == 2020 and float(value).is_integer() else f"{value:,.2f}"


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


# ---------------------------------------------------------------------------
# League art (app/static/league/assets.csv)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _assets():
    if not ASSETS_CSV.exists():
        return []
    with open(ASSETS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["season_from"] = int(r["season_from"]) if r.get("season_from") else None
        r["season_to"] = int(r["season_to"]) if r.get("season_to") else None
    return rows


def art(code, kind="logo", season=None, facing=None):
    """URL of a franchise's art (logo, helmet, endzone_banner...) for a season, or None.
    A season with no art of its own uses the newest art for now (Jaden, 2026-11-15); once older art is
    added to assets.csv with season ranges, those seasons pick it up automatically."""
    rows = [r for r in _assets() if r["franchise_code"] == code and r["asset_type"] == kind
            and (facing is None or r["facing"] == facing)]
    if not rows:
        return None
    if season is not None:
        fits = [r for r in rows if (r["season_from"] or 0) <= season <= (r["season_to"] or 9999)]
        if fits:
            return ART_URL + max(fits, key=lambda r: r["season_from"] or 0)["file_name"]
    return ART_URL + max(rows, key=lambda r: r["season_from"] or 0)["file_name"]


def league_art(asset_id=None, kind=None, season=None):
    """League-wide art: by asset_id (league_logo), or by kind for one season (special_logo for 2026)."""
    for r in _assets():
        if r["franchise_code"]:
            continue
        if asset_id and r["asset_id"] == asset_id:
            return ART_URL + r["file_name"]
        if kind and r["asset_type"] == kind and season is not None and \
                (r["season_from"] or 0) <= season <= (r["season_to"] or 9999):
            return ART_URL + r["file_name"]
    return None


def team_colors(code):
    """(primary, secondary) hex colors from the franchise's main logo row; plain navy for retired teams."""
    for r in _assets():
        if r["franchise_code"] == code and r["asset_type"] == "logo" and r["primary_color"]:
            return r["primary_color"], r["secondary_color"] or r["primary_color"]
    return NO_COLORS


def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(hex_color):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def text_on(fill):
    """Light or dark text, whichever reads better on this fill."""
    return TEXT if contrast(TEXT, fill) >= contrast(BG, fill) else BG


def mix(scale, t):
    """Color at t (0..1) along a list of hex stops, interpolated in RGB (same as Altair's interpolate='rgb')."""
    t = min(max(t, 0.0), 1.0) * (len(scale) - 1)
    i = min(int(t), len(scale) - 2)
    a, b = _rgb(scale[i]), _rgb(scale[i + 1])
    f = t - i
    return "#" + "".join(f"{round(x + (y - x) * f):02X}" for x, y in zip(a, b))


def rgba(hex_color, alpha):
    r, g, b = _rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------
# HTML building blocks (shown with st.html)
# ---------------------------------------------------------------------------

def esc(value):
    return _html.escape(text(value))


def badge(code, size=28):
    """Plain shield in team colors with the team code: for franchises with no art (MON, UPB, MIS).
    Drawn as an SVG image (st.html strips inline <svg>, but keeps <img> with a data: address)."""
    primary, secondary = team_colors(code)
    height = round(size * 72 / 64)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 72" width="64" height="72">'
           f'<path d="M32 3 L59 11 V37 C59 54 46 64 32 69 C18 64 5 54 5 37 V11 Z" fill="{primary}" '
           f'stroke="{secondary}" stroke-width="3.5" stroke-linejoin="round"/>'
           f'<text x="32" y="44" text-anchor="middle" font-family="Arial Narrow, Arial, sans-serif" font-weight="700" '
           f'font-size="19" fill="#FFFFFF">{_html.escape(code)}</text></svg>')
    data = base64.b64encode(svg.encode()).decode()
    return (f'<img class="badge" src="data:image/svg+xml;base64,{data}" alt="{esc(full_name(code))}" '
            f'style="width:{size}px;height:{height}px">')


def logo(code, season=None, size=28, alt=""):
    """The franchise's logo as an <img>, or its plain shield when there's no art."""
    url = art(code, "logo", season)
    if not url:
        return badge(code, size)
    return f'<img class="lg" src="{url}" alt="{esc(alt)}" style="width:{size}px;height:{size}px">'


def team(code, season=None, label=None, size=26, name="short"):
    """Logo + name, for table cells and lists."""
    if label is None:          # no art to tell teams apart (e.g. the two Up North teams): use the full name
        label = full_name(code) if name == "full" or not art(code, "logo") else city(code)
    return f'<span class="tc">{logo(code, season, size)}<span>{esc(label)}</span></span>'


def chip(label, kind="gold"):
    return f'<span class="chip {kind}">{esc(label)}</span>'


POSITION_COLORS = {"QB": ("#e66767", "#F59A9A"), "RB": ("#199e70", "#4FD6A6"), "WR": ("#3987e5", "#8CC2FF"),
                   "TE": ("#c98500", "#F2BC57"), "K": ("#9085e9", "#B9B1F5"), "DEF": ("#8592A8", "#C3CBD8")}


def position_chip(position):
    """QB / RB / WR ... as a small tinted label (the letters carry the meaning; color just helps scanning)."""
    position = text(position)
    if not position:
        return ""
    fill, ink = POSITION_COLORS.get(position, ("#8592A8", "#C3CBD8"))
    return f'<span class="poschip" style="background:{rgba(fill, .2)};color:{ink}">{esc(position)}</span>'


def streak_chip(value):
    """'W3' / 'L2' as a small colored chip (the letter carries the meaning, not just the color)."""
    if not value:
        return ""
    return f'<span class="{"pos" if value.startswith("W") else "neg" if value.startswith("L") else "muted"}">{esc(value)}</span>'


def html(markup):
    st.html(markup)


def page_header(title, lede=None, logo_url=None):
    pic = f'<img src="{logo_url}" alt="" class="hdr-logo">' if logo_url else ""
    lede_html = f'<p class="lede">{esc(lede)}</p>' if lede else ""
    html(f'<header class="hdr">{pic}<div><h1 class="title">{esc(title)}</h1>{lede_html}</div></header>')


def section(title, note=None):
    note_html = f'<span class="secnote">{note}</span>' if note else ""
    html(f'<div class="secrow"><h2 class="sec">{esc(title)}</h2>{note_html}</div>')


def html_table(header, rows, compact=False, cut_after=None, cut_label=None):
    """A styled table. header: [(label, 'l'|'r')]; rows: lists of cell HTML (already escaped/built).
    cut_after: draw a dashed line with cut_label after that many rows (e.g. the playoff line)."""
    aligns = [a for _, a in header]
    head = "".join(f'<th scope="col" class="{"num" if a == "r" else ""}">{esc(label)}</th>' for label, a in header)
    body = []
    for i, cells in enumerate(rows, 1):
        body.append("<tr>" + "".join(f'<td class="{"num" if a == "r" else ""}">{c}</td>'
                                     for c, a in zip(cells, aligns)) + "</tr>")
        if cut_after and i == cut_after and i < len(rows):
            body.append(f'<tr class="cut"><td colspan="{len(header)}"><div class="cutline">{esc(cut_label or "")}</div></td></tr>')
    return (f'<div class="tbl-wrap"><table class="std{" compact" if compact else ""}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def stat_strip(items):
    """One card of big numbers with small labels: [(value, label), ...]."""
    cells = "".join(f'<div class="stat"><span class="stat-v">{esc(v)}</span><span class="stat-l">{esc(l)}</span></div>'
                    for v, l in items)
    return f'<section class="card strip">{cells}</section>'


def hero(left, right, label="", sublabel="", note_left="", note_right="", winner="auto"):
    """The big matchup card: helmets face each other, each team's color glows in from its side.
    left / right: dict(code, score, season, record, label). winner: 'auto' (higher score), 'l', 'r' or None
    (no winner, e.g. the 2022 co-champions)."""
    lp, rp = team_colors(left["code"])[0], team_colors(right["code"])[0]
    bg = (f"background: radial-gradient(ellipse 46% 120% at 0% 55%, {rgba(lp, .85)} 0%, {rgba(lp, 0)} 100%), "
          f"radial-gradient(ellipse 46% 120% at 100% 55%, {rgba(rp, .85)} 0%, {rgba(rp, 0)} 100%), #121A29")
    ls, rs = left.get("score"), right.get("score")
    known = ls is not None and rs is not None and not pd.isna(ls) and not pd.isna(rs)
    if winner == "auto":
        winner = None if not known or ls == rs else ("l" if ls > rs else "r")
    marker = {"l": '<span class="tri-l"></span>', "r": '<span class="tri-r"></span>'}.get(winner, "")
    margin = f"by {abs(ls - rs):.2f}" if known and winner else ("No winner" if known else "")

    def side(t, where):
        facing = "right" if where == "l" else "left"
        url = art(t["code"], "helmet", t.get("season"), facing)
        pic = (f'<img class="helmet" src="{url}" alt="{esc(full_name(t["code"]))} helmet">' if url
               else f'<div class="helmet-badge">{badge(t["code"], 96)}</div>')
        return f'<div class="tm {where}">{pic}{name_block(t, where)}</div>'

    def name_block(t, where):
        note = note_left if where == "l" else note_right
        return (f'<div class="tm-text"><div class="tname">{esc(t.get("label") or full_name(t["code"]))}</div>'
                f'<div class="trec">{esc(t.get("record") or "")}{note}</div></div>')

    def score(value, mine):
        if value is None or pd.isna(value):
            return '<span class="big lose">-</span>'
        dim = winner is not None and winner != mine
        return f'<span class="big{" lose" if dim else ""}">{pts(value, left.get("season"))}</span>'

    meta = (chip(label) if label else "") + (f'<span class="muted small">{esc(sublabel)}</span>' if sublabel else "")
    return (f'<section class="hero" aria-label="{esc(full_name(left["code"]))} {esc(pts(ls))}, '
            f'{esc(full_name(right["code"]))} {esc(pts(rs))}"><div class="hero-in" style="{bg}">'
            f'<div class="hero-meta">{meta}</div><div class="hero-grid">{side(left, "l")}'
            f'<div class="scores">{score(ls, "l")}<div class="mid">{marker}<span>Final</span>'
            f'<span>{esc(margin)}</span></div>{score(rs, "r")}</div>{side(right, "r")}</div>'
            f'<div class="hero-names"><div class="tm l">{name_block(left, "l")}</div>'
            f'<div class="tm r">{name_block(right, "r")}</div></div></div></section>')


def score_tile(a, b, season=None, footer_left="Final", tags=""):
    """A small result card. a / b: dict(code, score, record). Winner on top with a marker; loser dimmed."""
    sa, sb = a.get("score"), b.get("score")
    known = sa is not None and sb is not None and not pd.isna(sa) and not pd.isna(sb)
    if known and sb > sa:
        a, b, sa, sb = b, a, sb, sa
    tie = known and sa == sb
    winner_known = a.get("won") if a.get("won") is not None else (known and not tie)

    def row(t, score, top):
        cls = "w" if top and winner_known else ("" if not winner_known else "l")
        rec = f'<span class="rc">{esc(t.get("record") or "")}</span>' if t.get("record") else ""
        mark = '<span class="mk"></span>' if top and winner_known else "<span></span>"
        return (f'<div class="trow {cls}">{logo(t["code"], season, 28)}<span class="nm">{esc(city(t["code"]))}{rec}</span>'
                f'<span class="sc">{pts(score, season) if score is not None else ""}</span>{mark}</div>')

    margin = f"by {abs(sa - sb):.2f}" if known and not tie else ("Tie" if tie else "")
    return (f'<article class="tile">{row(a, sa, True)}{row(b, sb, False)}'
            f'<div class="tfoot"><span>{esc(footer_left)}{tags}</span><span>{esc(margin)}</span></div></article>')


def tiles(cards, columns=2):
    return f'<div class="tiles c{columns}">{"".join(cards)}</div>'


# ---------------------------------------------------------------------------
# The page style (called once per run from streamlit_app.py)
# ---------------------------------------------------------------------------

CSS = f"""
<style>
@font-face {{ font-family: 'Archivo'; src: url('app/static/fonts/archivo.woff2') format('woff2-variations');
  font-weight: 100 900; font-stretch: 62% 125%; font-display: swap; }}
:root {{ --nfl-accent: {ACCENT}; }}
.stApp, .stApp button, .stApp input {{ font-variant-numeric: tabular-nums; }}
[data-testid="stMainBlockContainer"] {{ max-width: 1240px; padding-top: 4.2rem; padding-bottom: 4rem; }}
[data-testid="stHeading"] h1, [data-testid="stHeading"] h2, [data-testid="stHeading"] h3 {{
  font-weight: 800; font-variation-settings: 'wdth' 70; letter-spacing: .005em; }}
[data-testid="stSidebarNav"] a span {{ font-size: 0.95rem; }}
[data-testid="stSidebarHeader"] img {{ height: 3.1rem; max-width: 100%; }}
[data-testid="stCaptionContainer"] {{ color: {MUTED}; }}
[data-testid="stExpander"] details {{ background: {CARD}; border-color: {LINE}; }}
.muted {{ color: {MUTED}; }} .small {{ font-size: 13px; }}
/* page header and section titles */
.hdr {{ display: flex; align-items: center; gap: 20px; margin: 0 0 4px; }}
.hdr-logo {{ width: 64px; height: auto; }}
.title {{ font-size: 46px; line-height: .95; margin: 0; font-weight: 800; font-variation-settings: 'wdth' 66;
  letter-spacing: .003em; color: {TEXT}; padding: 0; }}
.lede {{ margin: 8px 0 0; color: {TEXT_SECONDARY}; font-size: 16px; max-width: 70ch; }}
.secrow {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin: 10px 0 0; }}
.sec {{ font-size: 25px; line-height: 1.1; margin: 0; padding: 0; font-weight: 780; font-variation-settings: 'wdth' 72; color: {TEXT}; }}
.secnote {{ color: {MUTED}; font-size: 13px; }}
/* pieces */
.card {{ background: {CARD}; border: 1px solid #232E45; border-radius: 14px; }}
.chip {{ display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px; font-size: 12.5px;
  font-weight: 600; white-space: nowrap; }}
.chip.gold {{ background: rgba(243,197,74,.14); color: {ACCENT}; }}
.chip.plain {{ background: #202B42; color: #C3CBD8; }}
.poschip {{ display: inline-block; min-width: 30px; text-align: center; padding: 2px 6px; border-radius: 6px; font-size: 11.5px; font-weight: 700; letter-spacing: .02em; }}
.pos {{ color: #7FBCFF; font-weight: 700; }} .neg {{ color: #F08585; font-weight: 700; }}
.lg {{ object-fit: contain; flex: none; display: block; }}
.badge {{ flex: none; display: block; }}
.tc {{ display: inline-flex; align-items: center; gap: 10px; font-weight: 600; white-space: nowrap; }}
/* stat strip */
.strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }}
.stat {{ padding: 16px 22px; display: flex; flex-direction: column; gap: 4px; }}
.stat + .stat {{ border-left: 1px solid #222D43; }}
.stat-v {{ font-weight: 800; font-variation-settings: 'wdth' 62; font-size: 40px; line-height: .95; color: {TEXT}; }}
.stat-l {{ color: {MUTED}; font-size: 13px; }}
/* hero matchup */
.hero {{ position: relative; border-radius: 22px; overflow: hidden; border: 1px solid {LINE}; color: {TEXT}; }}
.hero-in {{ position: relative; padding: 20px 26px 24px; }}
.hero-in::before {{ content: ''; position: absolute; inset: 0; pointer-events: none;
  background: repeating-linear-gradient(90deg, transparent 0 calc(10% - 1px), rgba(255,255,255,.045) calc(10% - 1px) 10%); }}
.hero-in::after {{ content: ''; position: absolute; top: 0; bottom: 0; left: 50%; width: 2px; margin-left: -1px;
  background: rgba(255,255,255,.07); pointer-events: none; }}
.hero-meta {{ position: relative; z-index: 1; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; min-height: 24px; }}
.hero-grid {{ position: relative; z-index: 1; display: grid; grid-template-columns: minmax(0,1fr) auto minmax(0,1fr);
  align-items: center; gap: 12px; margin-top: 6px; }}
.tm {{ display: flex; flex-direction: column; gap: 8px; min-width: 0; }}
.tm.r {{ align-items: flex-end; text-align: right; }}
.helmet {{ width: 168px; max-width: 100%; height: auto; filter: drop-shadow(0 10px 18px rgba(0,0,0,.45)); }}
.helmet-badge {{ padding: 8px 20px; }}
.tname {{ font-weight: 800; font-variation-settings: 'wdth' 72; font-size: 26px; line-height: 1.05; }}
.trec {{ color: #C3CBD8; font-size: 14px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
.tm.r .trec {{ justify-content: flex-end; }}
.hero-names {{ display: none; }}
.series {{ padding: 20px 24px; display: grid; grid-template-columns: minmax(0,1fr) auto minmax(0,1fr); align-items: center; gap: 16px; }}
.series .s-l {{ display: flex; align-items: center; gap: 14px; }}
.series .s-r {{ display: flex; align-items: center; gap: 14px; justify-content: flex-end; text-align: right; }}
.series .s-m {{ text-align: center; }}
.scores {{ display: flex; align-items: center; gap: 22px; }}
.big {{ font-weight: 800; font-variation-settings: 'wdth' 62; font-size: 92px; line-height: .9; letter-spacing: -.01em; }}
.big.lose {{ color: rgba(242,245,250,.5); }}
.mid {{ display: flex; flex-direction: column; align-items: center; gap: 6px; min-width: 64px; color: #C3CBD8;
  font-size: 13px; font-weight: 600; }}
.tri-l {{ width: 0; height: 0; border-top: 7px solid transparent; border-bottom: 7px solid transparent; border-right: 11px solid {TEXT}; }}
.tri-r {{ width: 0; height: 0; border-top: 7px solid transparent; border-bottom: 7px solid transparent; border-left: 11px solid {TEXT}; }}
/* score tiles */
.tiles {{ display: grid; gap: 12px; }}
.tiles.c2 {{ grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }}
.tiles.c3 {{ grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
.tile {{ background: {CARD}; border: 1px solid #232E45; border-radius: 12px; padding: 12px 14px 10px; display: flex;
  flex-direction: column; gap: 6px; color: {TEXT}; }}
.trow {{ display: grid; grid-template-columns: 28px minmax(0,1fr) auto 12px; align-items: center; gap: 10px; }}
.trow .nm {{ font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.trow .rc {{ color: {MUTED}; font-size: 12.5px; font-weight: 500; margin-left: 6px; }}
.trow .sc {{ font-weight: 800; font-variation-settings: 'wdth' 66; font-size: 24px; line-height: 1; }}
.trow.l .nm {{ color: {TEXT_SECONDARY}; font-weight: 500; }}
.trow.l .sc {{ color: rgba(242,245,250,.5); }}
.mk {{ width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 8px solid {TEXT}; justify-self: end; }}
.tfoot {{ display: flex; justify-content: space-between; gap: 8px; border-top: 1px solid #222D43; padding-top: 8px;
  margin-top: 2px; font-size: 12.5px; color: {MUTED}; }}
.tfoot .chip {{ margin-left: 8px; padding: 1px 8px; }}
/* tables */
.tbl-wrap {{ background: {CARD}; border: 1px solid #232E45; border-radius: 14px; padding: 4px 4px 6px; overflow-x: auto; }}
table.std {{ width: 100%; border-collapse: collapse; font-size: 14px; color: {TEXT}; }}
table.std th {{ text-align: left; font-size: 12.5px; font-weight: 600; color: {MUTED}; padding: 10px 12px; border-bottom: 1px solid {LINE};
  white-space: nowrap; }}
table.std td {{ padding: 6px 12px; border-bottom: 1px solid #1E283C; vertical-align: middle; }}
table.std.compact td {{ padding: 5px 10px; }}
table.std tr:last-child td {{ border-bottom: 0; }}
table.std .num {{ text-align: right; white-space: nowrap; }}
table.std td strong {{ white-space: nowrap; }}
table.std .big-n {{ font-weight: 800; font-variation-settings: 'wdth' 66; font-size: 17px; }}
table.std .dim {{ color: rgba(242,245,250,.5); }}
table.std .yr {{ font-weight: 800; font-variation-settings: 'wdth' 66; font-size: 19px; }}
tr.cut td {{ padding: 0 !important; border-bottom: 0 !important; }}
.cutline {{ display: flex; align-items: center; gap: 10px; color: {MUTED}; font-size: 11.5px; padding: 2px 12px; }}
.cutline::before, .cutline::after {{ content: ''; flex: 1; border-top: 1px dashed #3A4763; }}
.bar {{ position: relative; height: 8px; border-radius: 4px; background: #1F2A40; min-width: 90px; }}
.bar .mid0 {{ position: absolute; top: -2px; bottom: -2px; left: 50%; width: 1px; background: #4A5878; }}
.bar .up {{ position: absolute; top: 0; bottom: 0; left: 50%; border-radius: 0 4px 4px 0; background: {SERIES_BLUE}; }}
.bar .down {{ position: absolute; top: 0; bottom: 0; right: 50%; border-radius: 4px 0 0 4px; background: {NEG_RED}; }}
/* champions */
.champs {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(128px, 1fr)); gap: 12px; }}
.champ {{ background: {CARD}; border: 1px solid #232E45; border-radius: 14px; padding: 16px 14px; display: flex;
  flex-direction: column; gap: 10px; min-height: 190px; box-sizing: border-box; color: {TEXT}; }}
.champ .yr {{ font-weight: 800; font-variation-settings: 'wdth' 62; font-size: 34px; line-height: .9; }}
.champ .who {{ font-weight: 700; line-height: 1.2; }}
.champ .how {{ font-size: 12.5px; color: {MUTED}; line-height: 1.35; }}
.champ.next {{ background: linear-gradient(180deg, #1E2740, {CARD}); border-color: {ACCENT}; }}
/* franchise banner */
.banner {{ border-radius: 22px; overflow: hidden; border: 1px solid {LINE}; background: #0A0F19; height: 290px; }}
.banner img {{ width: 100%; height: 100%; object-fit: cover; object-position: center 46%; display: block; }}
.ident {{ display: flex; align-items: flex-end; gap: 22px; margin: -64px 0 0 28px; position: relative; flex-wrap: wrap; }}
.ident-logo {{ width: 132px; height: 132px; border-radius: 26px; background: {CARD}; border: 1px solid #2A3650; display: grid;
  place-items: center; box-shadow: 0 14px 30px rgba(0,0,0,.45); }}
.ident-text {{ padding-bottom: 6px; display: flex; flex-direction: column; gap: 8px; }}
/* trades */
.trade {{ background: {CARD}; border: 1px solid #232E45; border-radius: 14px; padding: 14px 16px; color: {TEXT}; }}
.trade-head {{ display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; color: {MUTED}; font-size: 13px; margin-bottom: 10px; }}
.trade-sides {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
.trade-side {{ background: #131B2A; border-radius: 10px; padding: 10px 12px; }}
.trade-side ul {{ margin: 8px 0 0; padding-left: 18px; color: #D5DBE6; font-size: 14px; }}
.trade-side li {{ margin: 2px 0; }}
.trade-foot {{ margin-top: 10px; font-size: 13px; color: {TEXT_SECONDARY}; }}
@media (max-width: 640px) {{
  .title {{ font-size: 34px; }}
  .helmet {{ width: 54px; }}
  .helmet-badge {{ padding: 0; }}
  .big {{ font-size: 34px; }}
  .tname {{ font-size: 17px; }}
  .trec {{ font-size: 12px; }}
  .scores {{ gap: 8px; }}
  .mid {{ min-width: 30px; font-size: 10.5px; gap: 3px; }}
  .hero-in {{ padding: 14px 12px 16px; }}
  .hero-grid {{ gap: 6px; }}
  .hero-grid .tm-text {{ display: none; }}
  .hero-names {{ position: relative; z-index: 1; display: flex; justify-content: space-between; gap: 12px; margin-top: 10px; }}
  .series {{ grid-template-columns: 1fr 1fr; padding: 16px; }}
  .series .s-m {{ grid-column: 1 / -1; grid-row: 1; }}
  .series .s-l, .series .s-r {{ flex-direction: column; align-items: center; text-align: center; justify-content: flex-start; gap: 8px; }}
  .series .s-r {{ flex-direction: column-reverse; justify-content: flex-end; }}
  .stat + .stat {{ border-left: 0; }}
  .banner {{ height: 170px; }}
  .ident {{ margin: -48px 0 0 14px; }}
  .ident-logo {{ width: 96px; height: 96px; }}
}}
</style>
"""


def style():
    """The league look: fonts, cards, tables. Called once per run, before the page."""
    st.html(CSS)
    wordmark = PROJECT_ROOT / "app" / "static" / "brand" / "sidebar_wordmark.png"
    shield = league_art("league_logo")
    if wordmark.exists():
        st.logo(str(wordmark), size="large", icon_image=str(PROJECT_ROOT / shield) if shield else None)


# ---------------------------------------------------------------------------
# Altair defaults for the dark page
# ---------------------------------------------------------------------------

def dark_chart(chart, **config):
    """Shared axis/legend styling for charts on the dark page (use with st.altair_chart(..., theme=None))."""
    return (chart.configure(background="transparent", font="Archivo", **config)
            .configure_view(stroke=None)
            .configure_axis(labelColor=TEXT_SECONDARY, titleColor=MUTED, gridColor=LINE, domainColor=LINE,
                            tickColor=LINE, labelFontSize=12, titleFontSize=12, titleFontWeight=500)
            .configure_legend(labelColor=TEXT_SECONDARY, titleColor=MUTED, labelFontSize=12, titleFontSize=12,
                              titleFontWeight=500))


def show_chart(chart, **kwargs):
    kwargs.setdefault("width", "stretch")
    st.altair_chart(dark_chart(chart), theme=None, **kwargs)


# ---------------------------------------------------------------------------
# Small season helpers shared by pages
# ---------------------------------------------------------------------------

def records_through(season, week):
    """{code: 'W-L'}: each team's regular-season record after `week` of `season` (official games)."""
    games = team_games()
    done = games[(games["season"] == season) & (games["game_type"] == "regular") & (games["week"] <= week)]
    out = {}
    for code, g in done.groupby("franchise_code"):
        out[code] = record((g["result"] == "W").sum(), (g["result"] == "L").sum(), (g["result"] == "T").sum())
    return out


def streaks(season):
    """{code: 'W3'}: each team's current regular-season streak."""
    games = team_games()
    part = games[(games["season"] == season) & (games["game_type"] == "regular")].sort_values("week")
    out = {}
    for code, g in part.groupby("franchise_code"):
        results = [r for r in g["result"] if isinstance(r, str)]
        if not results:
            continue
        last, n = results[-1], 0
        for r in reversed(results):
            if r != last:
                break
            n += 1
        out[code] = f"{last}{n}"
    return out


def roman(n):
    out = ""
    for value, letters in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += letters
            n -= value
    return out
