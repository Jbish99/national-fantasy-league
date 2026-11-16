"""
app/views/identities.py
Team identities (Phase 10): every team-season's style label (how it scored) and build label (how its roster
was put together), the league style map, all-time identities per franchise, and how the labels work.

Rules (db/schema/016_team_identities.sql, decisions by Jaden 2026-11-16): regular season, starters only, 2021 on.
A team earns a label when it is at least 1 standard deviation from that season's league average in the label's
direction; the strongest qualifying label of each kind wins, else the fallback (Well-Rounded / Mixed Build).
All-time = the label a franchise leaned toward most on average over 2+ seasons (at least 0.5 SD).
Label names, descriptions and thresholds: core.identity (rows in db/seed/001_reference_data.sql).
"""

import altair as alt
import pandas as pd
import streamlit as st

from lib import (BG, MUTED, SERIES_BLUE, TEXT, art, city, esc, footer, full_name, html, html_table,
                 identity_evidence, identity_row, identity_value, ordinal, page_header, query, record, require,
                 section, show_chart, team)

page_header("Team identities",
            "Every team-season gets two labels: a style (how it scored) and a build (how its roster was put "
            "together). A team earns a label when it clearly stands out: at least 1 standard deviation from that "
            "season's league average (about the top or bottom 2 of 12). Regular season, starters only, 2021 on.")
require("core.v_team_identity")

ident = query("select * from core.identity order by sort_order")
labels = query("select * from core.v_team_identity")
metrics = query("select * from core.v_team_metric")
alltime = query("select * from core.v_franchise_identity")
standings = query("select season, franchise_code, wins, losses, ties, regular_season_rank from core.v_season_standings")

if labels.empty or ident.empty:
    st.info("No identities yet: they need Sleeper seasons (2021 on) and the labels in db/seed/001_reference_data.sql "
            "(run `python -m etl.run_sql --all`).")
    footer()
    st.stop()

by_code = {r["identity_code"]: r for r in ident.to_dict("records")}

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


def team_card(r, season, extra=""):
    top = (f'<div class="id-top">{team(r.franchise_code, season, name="full")}'
           f'<span class="muted">{extra}</span></div>')
    return (f'<div class="idcard">{top}'
            + identity_row("Style", r.style_name, why(r.style_code, r.style_value, r.style_league_avg),
                           is_fallback(r.style_code))
            + identity_row("Build", r.build_name, why(r.build_code, r.build_value, r.build_league_avg),
                           is_fallback(r.build_code))
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
        cards.append(team_card(r, season, rec + rank))
    html(f'<div class="idgrid">{"".join(cards)}</div>')

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
        mp["Style"] = mp["franchise_code"].map(names["style_name"])
        mp["Build"] = mp["franchise_code"].map(names["build_name"])
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
        tip = [alt.Tooltip("team_full:N", title="Team"), alt.Tooltip("Style:N"), alt.Tooltip("Build:N"),
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
            rows.append([team(r.franchise_code, season)] + [
                "" if vals is None else identity_value(vals.get(m), f) for _, m, f in COLUMNS])
        rows.append(['<strong>League average</strong>'] + [
            f"<strong>{identity_value(avgs.get(m), f)}</strong>" for _, m, f in COLUMNS])
        html(f'<div class="tight">{html_table([("Team", "l")] + [(h, "r") for h, _, _ in COLUMNS], rows, compact=True)}</div>')
        st.caption("Shares are of the starters' points in the regular season. Through the air = passing and catching "
                   "points as a share of passing + catching + running points (rebuilt from real stat lines). "
                   "Weekly swing = standard deviation of the weekly score ÷ its average. Drafted / Kept / Traded for / "
                   "Pickups = how each starter got onto the roster.")

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
        lines = "".join(f"<div><b>{s.season}</b> &nbsp;{esc(s.style_name)} · {esc(s.build_name)}</div>"
                        for s in hist[hist["franchise_code"] == r.franchise_code].itertuples())
        span = f"{int(r.first_season)}-{str(int(r.last_season))[-2:]}"
        cards.append(
            f'<div class="idcard"><div class="id-top">{team(r.franchise_code, None, name="full")}'
            f'<span class="muted">{int(r.seasons)} seasons · {span}</span></div>'
            + identity_row("Style", r.style_name, lean(r.style_code, r.style_avg_score, r.style_seasons_earned,
                                                       r.seasons), is_fallback(r.style_code))
            + identity_row("Build", r.build_name, lean(r.build_code, r.build_avg_score, r.build_seasons_earned,
                                                       r.seasons), is_fallback(r.build_code))
            + f'<div class="id-hist">{lines}</div></div>')
    html(f'<div class="idgrid">{"".join(cards)}</div>')
    st.caption("All-time = the style and build label each franchise leaned toward most, averaged over its seasons "
               "(at least half a standard deviation on average, 2+ seasons). A franchise can lean a way on average "
               "without ever having it as its top label in a single season.")

    with st.expander("Every season as a grid"):
        grid_seasons = sorted(labels["season"].unique().tolist())
        rows = []
        for code in at["franchise_code"]:
            mine = labels[labels["franchise_code"] == code].set_index("season")
            cells = []
            for s in grid_seasons:
                if s in mine.index:
                    m = mine.loc[s]
                    cells.append(f'<strong>{esc(m["style_name"])}</strong><br>'
                                 f'<span class="muted small">{esc(m["build_name"])}</span>')
                else:
                    cells.append('<span class="muted">-</span>')
            rows.append([team(code)] + cells)
        html(f'<div class="tight">{html_table([("Franchise", "l")] + [(str(s), "l") for s in grid_seasons], rows, compact=True)}</div>')

# --- how labels work -----------------------------------------------------------------------------
with rules_tab:
    section("How labels work")
    st.markdown(
        "- **Two labels per team-season.** A *style* (how the team scored) and a *build* (how its roster was put "
        "together).\n"
        "- **Earning one.** The team must clearly stand out: at least 1 standard deviation from that season's league "
        "average on the label's measure (roughly the top or bottom 2 of 12). If several labels qualify, the one where "
        "the team stands out most wins. If none does, it's **Well-Rounded** (style) or **Mixed Build** (build).\n"
        "- **What counts.** Regular season only, the starting lineup only, 2021 on (2020 was on ESPN, with no "
        "player-level data). A season still being played uses the weeks so far.\n"
        "- **All-time.** The label a franchise leaned toward most on average over its seasons, if it leaned that way "
        "by at least half a standard deviation (2+ seasons).")
    for kind, title in (("style", "Style labels: how a team scored"), ("build", "Build labels: how the roster was built")):
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
    st.caption("How a starter got onto the roster: the team's latest trade, waiver claim or free-agent pickup of him "
               "that season, otherwise kept, otherwise drafted. Through the air vs on the ground uses the real stat "
               "lines scored with that season's rules: QB scrambles count as on the ground, running backs' catches "
               "as through the air.")

footer()
