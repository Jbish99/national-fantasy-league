"""
app/views/identities.py
Team identities (Phase 10 + 10b): every team-season's scheme label (how it scored; kind 'style' in the
database), base formation (what it put in its FLEX spots) and front-office label (how its roster was put
together; kind 'build'), each with a scouting report in football voice (app/story.py), plus the league style
map, all-time identities per franchise, and how the labels work.

Rules (db/schema/016_team_identities.sql, decisions by Jaden 2026-11-16): regular season, starters only, 2021 on.
A team earns a label when it is at least 1 standard deviation from that season's league average in the label's
direction; the strongest qualifying label of each kind wins, else the fallback (Well-Rounded / Mixed Build).
All-time = the label a franchise leaned toward most on average over 2+ seasons (at least 0.5 SD).
Label names, descriptions and thresholds: core.identity; formation names: core.formation (both in
db/seed/001_reference_data.sql). Formations: db/schema/017_team_story.sql (Jaden 2026-11-16): the formation a team
lined up in for more than half its weeks, else Multiple. The page still works before 017 is applied (no formations
or reports, just the labels).
"""

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

from lib import (BG, MUTED, SERIES_BLUE, TEXT, art, city, esc, footer, full_name, html, html_table,
                 identity_evidence, identity_row, identity_value, ordinal, page_header, query, record, require,
                 section, show_chart, story_block, team)
from story import formation_mix, franchise_book, season_report

page_header("Team identities",
            "Every team-season gets a scheme (how it scored), a base formation (how it lined up) and a front-office "
            "label (how the roster was built), plus a scouting report. A scheme or front-office label is earned by "
            "clearly standing out: at least 1 standard deviation from that season's league average (about the top "
            "or bottom 2 of 12). Regular season, starters only, 2021 on.")
require("core.v_team_identity")

ident = query("select * from core.identity order by sort_order")
labels = query("select * from core.v_team_identity")
metrics = query("select * from core.v_team_metric")
alltime = query("select * from core.v_franchise_identity")
standings = query("select season, franchise_code, wins, losses, ties, regular_season_rank, made_playoffs, "
                  "final_place, champion from core.v_season_standings")
try:                                  # Phase 10b (db/schema/017): formations + the players the reports name
    formations = query("select * from core.v_team_formation")
    formations_all = query("select * from core.v_franchise_formation")
    formation_defs = query("select * from core.formation order by sort_order")
    players = query("select * from core.v_team_player_season")
except psycopg.Error:
    formations = formations_all = formation_defs = players = pd.DataFrame()

if labels.empty or ident.empty:
    st.info("No identities yet: they need Sleeper seasons (2021 on) and the labels in db/seed/001_reference_data.sql "
            "(run `python -m etl.run_sql --all`).")
    footer()
    st.stop()

by_code = {r["identity_code"]: r for r in ident.to_dict("records")}
formation_names = dict(zip(formation_defs.get("formation_code", []), formation_defs.get("name", [])))
form_by = {(r["season"], r["franchise_code"]): r for r in formations.to_dict("records")}
form_all_by = {r["franchise_code"]: r for r in formations_all.to_dict("records")}
champs = standings[standings["champion"].fillna(False).astype(bool)].groupby("season").size()
co_champion_seasons = set(champs[champs > 1].index)


def team_players(season, code):
    if players.empty:
        return players
    return players[(players["season"] == season) & (players["franchise_code"] == code)]

# What each measure means, in plain words (for the "How labels work" tab)
MEASURES = {
    "ground_share": "Share of its offense's points scored running the ball (QB scrambles count), vs passing and catching",
    "qb_share": "Share of its starters' points scored by its quarterback",
    "te_share": "Share of its starters' points scored by its tight end",
    "top2_share": "Share of its starters' points from its 2 best starters that season",
    "score_cv": "How much its weekly score swings around its average (standard deviation ÷ average)",
    "drafted_share": "Share of its starters' points from players it drafted that season",
    "kept_share": "Share of its starters' points from its keepers",
    "trade_share": "Share of its starters' points from players it traded for",
    "pickup_share": "Share of its starters' points from waiver and free-agent pickups",
    "avg_age": "Average age (on Sept 1) of its starters, per start",
    "bench_ppw": "Points scored on its bench per week",
}
# How the numbers table shows each measure: (header, metric, format)
COLUMNS = [("Through the air", "ground_share", "pct_inverse"), ("QB share", "qb_share", "pct"),
           ("Top 2 share", "top2_share", "pct"), ("Weekly swing", "score_cv", "pct"),
           ("Drafted", "drafted_share", "pct"), ("Kept", "kept_share", "pct"), ("Traded for", "trade_share", "pct"),
           ("Pickups", "pickup_share", "pct"), ("Starter age", "avg_age", "years"), ("Bench / week", "bench_ppw", "points")]


def why(code, value, league_avg):
    """The evidence sentence for a label, or its description when it's a fallback."""
    label = by_code.get(code)
    if label is None:
        return ""
    if label["metric"] is None or pd.isna(label["metric"]):
        return label["description"]
    return identity_evidence(label, value, league_avg)


def is_fallback(code):
    label = by_code.get(code)
    return label is None or label["metric"] is None or pd.isna(label["metric"])


def formation_why(form):
    """Hover text for a formation: '9 of 14 weeks · WR + WR in the FLEX'."""
    if not form or pd.isna(form.get("top_weeks")):
        return ""
    if form.get("is_fallback"):
        return "No formation in more than half its weeks: " + formation_mix(form, formation_names)
    row = formation_defs[formation_defs["formation_code"] == form["formation_code"]]
    flex = row["flex_set"].iloc[0] if not row.empty and isinstance(row["flex_set"].iloc[0], str) else ""
    return f"{int(form['top_weeks'])} of {int(form['weeks_counted'])} weeks" + (f" · {flex} in the FLEX" if flex else "")


def formation_row(form):
    if not form or not isinstance(form.get("formation_name"), str):
        return ""
    return identity_row("Formation", form["formation_name"], "", bool(form.get("is_fallback")), formation_why(form))


def team_card(r, season, standing, extra=""):
    """A team-season card: scheme, formation, front office, then the scouting report."""
    form = form_by.get((season, r.franchise_code))
    report = season_report(r._asdict(), form, team_players(season, r.franchise_code), standing,
                           metrics[metrics["season"] == season], by_code, formation_names,
                           co_champion=season in co_champion_seasons)
    top = (f'<div class="id-top">{team(r.franchise_code, season, name="full")}'
           f'<span class="muted">{extra}</span></div>')
    return (f'<div class="idcard">{top}'
            + identity_row("Scheme", r.style_name, "", is_fallback(r.style_code),
                           why(r.style_code, r.style_value, r.style_league_avg))
            + formation_row(form)
            + identity_row("Front office", r.build_name, "", is_fallback(r.build_code),
                           why(r.build_code, r.build_value, r.build_league_avg))
            + story_block(report)
            + "</div>")


seasons_list = sorted(labels["season"].unique().tolist(), reverse=True)
season_tab, alltime_tab, rules_tab = st.tabs(["By season", "All-time", "How labels work"])

# --- one season --------------------------------------------------------------------------------
with season_tab:
    left, _ = st.columns([1, 3])
    season = left.selectbox("Season", seasons_list)
    sl = labels[labels["season"] == season].merge(standings, on=["season", "franchise_code"], how="left")
    sl = sl.sort_values(["regular_season_rank", "franchise_code"], na_position="last")
    in_progress = not bool(sl["season_complete"].iloc[0])
    if in_progress:
        st.caption(f"{season} is still being played: these labels use the weeks so far and can still change.")

    section(f"{season} identities", "In standings order")
    cards = []
    for r in sl.itertuples():
        rec = record(r.wins, r.losses, r.ties) if pd.notna(r.wins) else ""
        rank = f" · {ordinal(r.regular_season_rank)}" if pd.notna(r.regular_season_rank) else ""
        standing = {"wins": r.wins, "losses": r.losses, "ties": r.ties, "regular_season_rank": r.regular_season_rank,
                    "made_playoffs": r.made_playoffs, "final_place": r.final_place, "champion": r.champion,
                    "season_complete": r.season_complete}
        cards.append(team_card(r, season, standing, rec + rank))
    html(f'<div class="idgrid">{"".join(cards)}</div>')
    st.caption("Point at a label for the numbers behind it. Scouting reports are written from the real numbers: "
               "regular season, starting lineups only.")

    # league style map: through the air vs top-heavy
    sm = metrics[metrics["season"] == season]
    wide = sm.pivot(index="franchise_code", columns="metric", values="value").reset_index()
    avgs = sm.groupby("metric")["league_avg"].first()
    if {"ground_share", "top2_share"} <= set(wide.columns) and wide["ground_share"].notna().any():
        section("League style map", "Where every team scored from, and how much it leaned on its top 2 starters")
        mp = wide[["franchise_code", "ground_share", "top2_share"]].dropna().copy()
        mp["air"] = (1 - mp["ground_share"].astype(float)) * 100
        mp["top2"] = mp["top2_share"].astype(float) * 100
        mp["Team"] = mp["franchise_code"].map(city)
        mp["team_full"] = mp["franchise_code"].map(full_name)
        mp["logo"] = [art(c, "logo", season) for c in mp["franchise_code"]]
        names = sl.set_index("franchise_code")
        mp["Scheme"] = mp["franchise_code"].map(names["style_name"])
        mp["Formation"] = [(form_by.get((season, c)) or {}).get("formation_name") or "" for c in mp["franchise_code"]]
        mp["Front office"] = mp["franchise_code"].map(names["build_name"])
        avg_air = (1 - float(avgs["ground_share"])) * 100
        avg_top2 = float(avgs["top2_share"]) * 100
        x0, x1 = min(mp["air"].min(), avg_air), max(mp["air"].max(), avg_air)
        y0, y1 = min(mp["top2"].min(), avg_top2), max(mp["top2"].max(), avg_top2)
        pad_x, pad_y = max((x1 - x0) * 0.08, 1), max((y1 - y0) * 0.10, 1)
        x0, x1, y0, y1 = x0 - pad_x, x1 + pad_x, y0 - pad_y, y1 + pad_y
        # names under the logos, nudged down until they don't overlap one already placed
        # (sized for the desktop chart, about 900 x 430 px)
        width_px, height_px = 900, 430
        px_x, px_y = (x1 - x0) / width_px, (y1 - y0) / height_px      # data units per pixel
        placed, label_y = [], {}
        for r in mp.sort_values("top2", ascending=False).itertuples():
            ly = r.top2 - 25 * px_y
            while any(abs(r.air - ax) < 72 * px_x and abs(ly - ay) < 13 * px_y for ax, ay in placed):
                ly -= 13 * px_y
            placed.append((r.air, ly))
            label_y[r.franchise_code] = ly
        mp["label_y"] = mp["franchise_code"].map(label_y)
        y0 = min(y0, mp["label_y"].min() - 8 * px_y)
        # every layer must use the same zoomed scales (layers merge their domains; a plain rule layer pulls in 0)
        sx = alt.Scale(domain=[x0, x1], nice=False, zero=False)
        sy = alt.Scale(domain=[y0, y1], nice=False, zero=False)
        x = alt.X("air:Q", title="Share of offense through the air (%) →", scale=sx)
        y = alt.Y("top2:Q", title="Top 2 starters' share of points (%) →", scale=sy)
        tip = [alt.Tooltip("team_full:N", title="Team"), alt.Tooltip("Scheme:N"), alt.Tooltip("Formation:N"),
               alt.Tooltip("Front office:N"),
               alt.Tooltip("air:Q", title="Through the air %", format=".1f"),
               alt.Tooltip("top2:Q", title="Top 2 share %", format=".1f")]
        rules = (alt.Chart(pd.DataFrame({"air": [avg_air]})).mark_rule(color=MUTED, strokeDash=[4, 4]).encode(x=x)
                 + alt.Chart(pd.DataFrame({"top2": [avg_top2]})).mark_rule(color=MUTED, strokeDash=[4, 4]).encode(y=y))
        with_art = mp[mp["logo"].notna()]
        no_art = mp[mp["logo"].isna()]
        layers = [rules]
        if not with_art.empty:
            layers.append(alt.Chart(with_art).mark_image(width=34, height=34).encode(x=x, y=y, url="logo:N",
                                                                                     tooltip=tip))
        if not no_art.empty:
            layers.append(alt.Chart(no_art).mark_circle(size=220, color=SERIES_BLUE, stroke=BG, strokeWidth=2)
                          .encode(x=x, y=y, tooltip=tip))
        layers.append(alt.Chart(mp).mark_text(fontSize=11.5, fontWeight=600, color=TEXT, baseline="middle")
                      .encode(x=x, y=alt.Y("label_y:Q", scale=sy), text="Team:N"))
        show_chart(alt.layer(*layers).properties(height=430))
        st.caption(f"Dashed lines = the league average ({avg_air:.0f}% through the air, top 2 starters "
                   f"{avg_top2:.0f}% of points). Right = more of the offense through the air (Air-Raid side); left = "
                   "more on the ground. Up = more top-heavy (Stars & Scrubs side); down = more spread out. "
                   "Hover a team for its labels.")

    with st.expander("All the numbers"):
        rows = []
        for r in sl.itertuples():
            vals = wide.set_index("franchise_code").loc[r.franchise_code] if r.franchise_code in set(wide["franchise_code"]) else None
            form = form_by.get((season, r.franchise_code)) or {}
            mix = formation_mix(form, formation_names, unit=False) if form else ""
            rows.append([team(r.franchise_code, season)] + [
                "" if vals is None else identity_value(vals.get(m), f) for _, m, f in COLUMNS]
                + [f'{esc(form.get("formation_name") or "")}<br><span class="muted small">{esc(mix)}</span>'])
        rows.append(['<strong>League average</strong>'] + [
            f"<strong>{identity_value(avgs.get(m), f)}</strong>" for _, m, f in COLUMNS] + [""])
        html(f'<div class="tight">{html_table([("Team", "l")] + [(h, "r") for h, _, _ in COLUMNS] + [("Formation (weeks)", "l")], rows, compact=True)}</div>')
        st.caption("Shares are of the starters' points in the regular season. Through the air = passing and catching "
                   "points as a share of passing + catching + running points (rebuilt from real stat lines). "
                   "Weekly swing = standard deviation of the weekly score ÷ its average. Drafted / Kept / Traded for / "
                   "Pickups = how each starter got onto the roster. Formation weeks = regular-season weeks in each "
                   "formation, from what was in the 2 FLEX spots.")

# --- all-time ------------------------------------------------------------------------------------
with alltime_tab:
    section("All-time identities", "The labels each franchise leaned toward most, across its seasons (2021 on)")
    at = alltime[alltime["seasons"] >= 2].copy()
    at = at.assign(name=at["franchise_code"].map(full_name)).sort_values("name")
    hist = labels.sort_values("season", ascending=False)
    cards = []
    for r in at.itertuples():
        def lean(code, avg_score, earned, seasons):
            if is_fallback(code):
                return by_code[code]["description"] if code in by_code else ""
            times = f"earned it {int(earned)} of {int(seasons)} seasons" if earned else "never its top label in a season"
            return f"Leaned this way by {avg_score:+.1f} standard deviations on average · {times}"
        def season_line(s):
            form = (form_by.get((s.season, s.franchise_code)) or {}).get("formation_name")
            parts = [s.style_name] + ([form] if isinstance(form, str) else []) + [s.build_name]
            return f"<div><b>{s.season}</b> &nbsp;{' · '.join(esc(p) for p in parts)}</div>"
        lines = "".join(season_line(s) for s in hist[hist["franchise_code"] == r.franchise_code].itertuples())
        span = f"{int(r.first_season)}-{str(int(r.last_season))[-2:]}"
        form = form_all_by.get(r.franchise_code)
        mine = players[players["franchise_code"] == r.franchise_code] if not players.empty else players
        book = franchise_book(r._asdict(), form, mine, by_code)
        cards.append(
            f'<div class="idcard"><div class="id-top">{team(r.franchise_code, None, name="full")}'
            f'<span class="muted">{int(r.seasons)} seasons · {span}</span></div>'
            + identity_row("Scheme", r.style_name, "", is_fallback(r.style_code),
                           lean(r.style_code, r.style_avg_score, r.style_seasons_earned, r.seasons))
            + formation_row(form)
            + identity_row("Front office", r.build_name, "", is_fallback(r.build_code),
                           lean(r.build_code, r.build_avg_score, r.build_seasons_earned, r.seasons))
            + story_block(book, "The book on them")
            + f'<div class="id-hist">{lines}</div></div>')
    html(f'<div class="idgrid">{"".join(cards)}</div>')
    st.caption("All-time scheme and front office = the label each franchise leaned toward most, averaged over its "
               "seasons (at least half a standard deviation on average, 2+ seasons). A franchise can lean a way on "
               "average without ever having it as its top label in a single season. All-time formation = the one it "
               "lined up in for more than half of all its weeks, else Multiple. Point at a label for the numbers.")

    with st.expander("Every season as a grid"):
        grid_seasons = sorted(labels["season"].unique().tolist())
        rows = []
        for code in at["franchise_code"]:
            mine = labels[labels["franchise_code"] == code].set_index("season")
            cells = []
            for s in grid_seasons:
                if s in mine.index:
                    m = mine.loc[s]
                    form = (form_by.get((s, code)) or {}).get("formation_name")
                    form_line = f'<span class="small">{esc(form)}</span><br>' if isinstance(form, str) else ""
                    cells.append(f'<strong>{esc(m["style_name"])}</strong><br>{form_line}'
                                 f'<span class="muted small">{esc(m["build_name"])}</span>')
                else:
                    cells.append('<span class="muted">-</span>')
            rows.append([team(code)] + cells)
        html(f'<div class="tight">{html_table([("Franchise", "l")] + [(str(s), "l") for s in grid_seasons], rows, compact=True)}</div>')

# --- how labels work -----------------------------------------------------------------------------
with rules_tab:
    section("How labels work")
    st.markdown(
        "- **Three labels per team-season.** A *scheme* (how the team scored), a *formation* (how it lined up) and a "
        "*front office* label (how its roster was put together), plus a scouting report written from its numbers.\n"
        "- **Earning a scheme or front-office label.** The team must clearly stand out: at least 1 standard deviation "
        "from that season's league average on the label's measure (roughly the top or bottom 2 of 12). If several "
        "labels qualify, the one where the team stands out most wins. If none does, it's **Well-Rounded** (scheme) or "
        "**Mixed Build** (front office).\n"
        "- **Formation.** Every team gets its base formation: what it put in its 2 FLEX spots in more than half of "
        "its weeks. If no formation reached half, it ran a **Multiple** offense.\n"
        "- **What counts.** Regular season only, the starting lineup only, 2021 on (2020 was on ESPN, with no "
        "player-level data). A season still being played uses the weeks so far.\n"
        "- **All-time.** Scheme and front office: the label a franchise leaned toward most on average over its "
        "seasons, if it leaned that way by at least half a standard deviation (2+ seasons). Formation: the one it "
        "lined up in for more than half of all its weeks.")
    for kind, title in (("style", "Scheme: how a team scored"), ("build", "Front office: how the roster was built")):
        section(title)
        rows = []
        for r in ident[ident["kind"] == kind].itertuples():
            if r.metric is None or pd.isna(r.metric):
                rows.append([f"<strong>{esc(r.name)}</strong>", esc(r.description), "",
                             '<span class="muted">No label above qualified</span>'])
                continue
            side = "above" if r.direction == 1 else "below"
            sd = f"{float(r.min_z):g} standard deviation{'' if float(r.min_z) == 1 else 's'}"
            floor = (f", and at least {identity_value(r.min_value, r.value_format)}"
                     if pd.notna(r.min_value) else "")
            rows.append([f"<strong>{esc(r.name)}</strong>", esc(r.description),
                         f'<span class="muted">{esc(MEASURES.get(r.metric, r.metric))}</span>',
                         esc(f"At least {sd} {side} the league average{floor}")])
        html(f'<div class="tight">{html_table([("Label", "l"), ("What it means", "l"), ("Measure", "l"), ("Earned when", "l")], rows)}</div>')
        if kind == "style" and not formation_defs.empty:
            section("Formation: how a team lined up")
            frows = [[f"<strong>{esc(f.name)}</strong>", esc(f.description),
                      esc(f.flex_set) if isinstance(f.flex_set, str) else '<span class="muted">No formation in more '
                                                                         'than half its weeks</span>']
                     for f in formation_defs.itertuples()]
            html(f'<div class="tight">{html_table([("Formation", "l"), ("What it means", "l"), ("In the 2 FLEX spots", "l")], frows)}</div>')
            st.caption("Counted weeks: regular season, with every FLEX spot filled by a RB, WR or TE (a week a "
                       "team left a FLEX empty doesn't count). Fullbacks count as running backs.")
    st.caption("How a starter got onto the roster: the team's latest trade, waiver claim or free-agent pickup of him "
               "that season, otherwise kept, otherwise drafted. Through the air vs on the ground uses the real stat "
               "lines scored with that season's rules: QB scrambles count as on the ground, running backs' catches "
               "as through the air.")

footer()
