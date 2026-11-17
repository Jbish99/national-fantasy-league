"""
app/views/franchises.py
One franchise's history: its end-zone banner, all-time numbers, season by season, scoring trend,
and results against every opponent.
"""

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

from lib import (CONTEXT_GRAY, SERIES_BLUE, TEXT_SECONDARY, art, badge, chip, esc, footer, full_name, game_label,
                 html, html_table, identity_evidence, identity_row, link, ordinal, pts, query, record, section,
                 show_chart, stat_strip, story_block, table, team, team_games, text)
from story import formation_mix, franchise_book, season_report

alltime = query("select * from core.v_all_time_standings order by franchise")
info_all = query("select franchise_code, home_city, founded_season, final_season from core.franchise").set_index("franchise_code")
codes = alltime["franchise_code"].tolist()
codes.sort(key=lambda c: (alltime.set_index("franchise_code").loc[c, "last_season"] < alltime["last_season"].max(), full_name(c)))
code = st.selectbox("Franchise", codes, format_func=full_name)
info = alltime[alltime["franchise_code"] == code].iloc[0]
standings = query("select * from core.v_season_standings")
mine = standings[standings["franchise_code"] == code].sort_values("season")
home = info_all.loc[code] if code in info_all.index else None
complete = query("select season from core.season where status = 'complete' or season = 2020")["season"].tolist()

# --- banner and name ------------------------------------------------------------------------
banner = art(code, "endzone_banner")
logo_url = art(code, "logo", px=104)
pic = (f'<img src="{logo_url}" alt="{esc(full_name(code))} logo" style="width:104px;height:104px;object-fit:contain">'
       if logo_url else badge(code, 92))
titles = mine[mine["champion"]]["season"].tolist()
retired = info.last_season < standings["season"].max()
years = f"{info.first_season}" + (f"-{info.last_season}" if info.last_season != info.first_season else "")
where = text(home["home_city"]) if home is not None else ""
blurb = (f"{where + '. ' if where else ''}"
         + (f"Played {years}." if retired else f"In the league since {info.first_season}."))
chips = "".join(chip(f"{s} champion") for s in titles)
banner_html = (f'<div class="banner"><img src="{banner}" alt="{esc(full_name(code))} end zone"></div>' if banner
               else '<div class="banner" style="height:150px;background:linear-gradient(90deg,#172033,#0E1421)"></div>')
html(f"""<section aria-label="{esc(full_name(code))}">{banner_html}
<div class="ident"><div class="ident-logo">{pic}</div><div class="ident-text">
<h1 class="title" style="font-size:52px">{esc(full_name(code))}</h1>
<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span class="muted">{esc(blurb)}</span>{chips}</div>
</div></div></section>""")

finished = mine[mine["season"].isin(complete)]
html(stat_strip([
    (record(info.wins, info.losses, info.ties), "Regular-season record"),
    (f"{info.points_per_game:.2f}", "Points per game"),
    (f"{int(finished['made_playoffs'].sum())} of {len(finished)}", "Playoff trips (finished seasons)"),
    (record(info.playoff_wins, info.playoff_losses), "Playoff record"),
    (str(int(info.championships)), "Title" if info.championships == 1 else "Titles"),
]))
owners = ", ".join(dict.fromkeys(o for o in mine.sort_values("season", ascending=False)["owner"] if isinstance(o, str)))
if owners:
    st.caption(f"Owner{'s' if ',' in owners else ''}: {owners}")

# --- season by season ------------------------------------------------------------------
section("Season by season")
rows = []
for r in mine.sort_values("season", ascending=False).itertuples():
    if r.champion:
        finish = chip("Champion")
    elif r.season not in complete:
        finish = chip("In progress", "plain")
    elif pd.notna(r.final_place):
        playoff = f" (playoffs {record(r.playoff_wins, r.playoff_losses)})" if r.made_playoffs else ""
        finish = f'{ordinal(r.final_place)}<span class="muted small">{playoff}</span>'
    else:
        finish = ""
    rows.append([f'<span class="yr">{r.season}</span>', team(code, r.season, label=text(r.team_name) or full_name(code)),
                 record(r.wins, r.losses, r.ties), pts(r.points_for, r.season), f"{r.points_per_game:.2f}",
                 ordinal(r.regular_season_rank), finish])
html(html_table([("Season", "l"), ("Team name", "l"), ("Record", "r"), ("Points for", "r"), ("Per game", "r"),
                 ("Reg. season", "r"), ("Finish", "l")], rows))

# --- identity (Phase 10 + 10b): all-time + every season's scheme, formation, front office and scouting report ---
try:
    ids = query("select * from core.v_team_identity where franchise_code = %s order by season desc", (code,))
    ids_all = query("select * from core.v_franchise_identity where franchise_code = %s", (code,))
    labels_def = {r["identity_code"]: r for r in query("select * from core.identity").to_dict("records")}
    league_metrics = query("select * from core.v_team_metric")
except psycopg.Error:
    ids = None                     # 016 not applied yet (run `python -m etl.run_sql --all`)
try:                               # Phase 10b (017): formations + the players the reports name
    forms = {r["season"]: r for r in query("select * from core.v_team_formation where franchise_code = %s",
                                           (code,)).to_dict("records")}
    form_all = query("select * from core.v_franchise_formation where franchise_code = %s", (code,))
    form_all = form_all.iloc[0].to_dict() if not form_all.empty else None
    form_defs = query("select * from core.formation order by sort_order")
    form_names = dict(zip(form_defs["formation_code"], form_defs["name"]))
    flex_sets = dict(zip(form_defs["formation_code"], form_defs["flex_set"]))
    my_players = query("select * from core.v_team_player_season where franchise_code = %s", (code,))
except psycopg.Error:
    forms, form_all, form_names, flex_sets, my_players = {}, None, {}, {}, pd.DataFrame()
if ids is not None and not ids.empty:
    section("Identity", "Scheme = how it scored, formation = how it lined up, front office = how the roster was "
                        "built (2021 on)")
    champs = standings[standings["champion"].fillna(False).astype(bool)].groupby("season").size()
    co_champion_seasons = set(champs[champs > 1].index)

    def _why(code_, value, avg):
        label = labels_def.get(code_)
        if label is None:
            return ""
        if label["metric"] is None or pd.isna(label["metric"]):
            return label["description"]
        return identity_evidence(label, value, avg)

    def _fallback(code_):
        label = labels_def.get(code_)
        return label is None or label["metric"] is None or pd.isna(label["metric"])

    def _formation(form):
        if not form or not isinstance(form.get("formation_name"), str):
            return ""
        if form.get("is_fallback"):
            hover = "No formation in more than half its weeks: " + formation_mix(form, form_names)
        else:
            flex = flex_sets.get(form["formation_code"])
            hover = (f"{int(form['top_weeks'])} of {int(form['weeks_counted'])} weeks"
                     + (f" · {flex} in the FLEX" if isinstance(flex, str) else ""))
        return identity_row("Formation", form["formation_name"], "", bool(form.get("is_fallback")), hover)

    cards = []
    if not ids_all.empty and ids_all["seasons"].iloc[0] >= 2:
        a = ids_all.iloc[0]
        book = franchise_book(a.to_dict(), form_all, my_players, labels_def)
        cards.append(f'<div class="idcard"><div class="id-top"><strong>All-time</strong><span class="muted">'
                     f'{int(a["seasons"])} seasons · {link("identities", "How labels work")}</span></div>'
                     + identity_row("Scheme", a["style_name"], "", _fallback(a["style_code"]))
                     + _formation(form_all)
                     + identity_row("Front office", a["build_name"], "", _fallback(a["build_code"]))
                     + story_block(book, "The book on them") + "</div>")
    by_season = mine.set_index("season")
    for r in ids.itertuples():
        note = "" if r.season_complete else "so far"
        st_row = by_season.loc[r.season].to_dict() if r.season in by_season.index else {}
        st_row["season_complete"] = r.season_complete
        report = season_report(r._asdict(), forms.get(r.season),
                               my_players[my_players["season"] == r.season] if not my_players.empty else my_players,
                               st_row, league_metrics[league_metrics["season"] == r.season], labels_def, form_names,
                               co_champion=r.season in co_champion_seasons)
        cards.append(f'<div class="idcard"><div class="id-top"><span class="yr">{r.season}</span>'
                     f'<span class="muted">{note}</span></div>'
                     + identity_row("Scheme", r.style_name, "", _fallback(r.style_code),
                                    _why(r.style_code, r.style_value, r.style_league_avg))
                     + _formation(forms.get(r.season))
                     + identity_row("Front office", r.build_name, "", _fallback(r.build_code),
                                    _why(r.build_code, r.build_value, r.build_league_avg))
                     + story_block(report) + "</div>")
    html(f'<div class="idgrid">{"".join(cards[:3])}</div>')     # all-time + the 2 newest seasons: one row
    if len(cards) > 3:
        with st.expander("Earlier seasons"):
            html(f'<div class="idgrid">{"".join(cards[3:])}</div>')

left, right = st.columns(2, gap="large")
# --- scoring trend vs the league ------------------------------------------------------------
with left:
    section("Points per game vs the league")
    league = standings.groupby("season").apply(
        lambda s: s["points_for"].sum() / (s["wins"] + s["losses"] + s["ties"]).sum(), include_groups=False)
    name = full_name(code)
    trend = pd.concat([
        pd.DataFrame({"Season": mine["season"].astype(str), "Points per game": mine["points_per_game"], "Series": name}),
        pd.DataFrame({"Season": league.index.astype(str), "Points per game": league.values.round(2),
                      "Series": "League average"}),
    ]).dropna()
    trend = trend[trend["Season"].isin(mine["season"].astype(str))]
    color = alt.Color("Series:N", scale=alt.Scale(domain=[name, "League average"], range=[SERIES_BLUE, CONTEXT_GRAY]),
                      legend=alt.Legend(title=None, orient="top"))
    base = alt.Chart(trend).encode(
        x=alt.X("Season:O", title=None, axis=alt.Axis(labelAngle=0, grid=False)),
        y=alt.Y("Points per game:Q", title="Points per game (regular season)", scale=alt.Scale(zero=False)),
        color=color,
        tooltip=["Series:N", "Season:O", alt.Tooltip("Points per game:Q", format=".2f")],
    )
    lines = base.mark_line(strokeWidth=2.5).encode(
        strokeDash=alt.condition(alt.datum.Series == "League average", alt.value([5, 4]), alt.value([1, 0])))
    points = base.mark_point(filled=True, size=70, opacity=1)
    show_chart((lines + points).properties(height=320))
    st.caption("Regular-season points per game each season. Dashed gray: league average that season.")

# --- against each opponent -------------------------------------------------------------------
with right:
    games = team_games()
    games = games[games["franchise_code"] == code]
    section("Against each opponent", "All games")
    vs = games.groupby("opponent_code").agg(
        W=("result", lambda s: (s == "W").sum()), L=("result", lambda s: (s == "L").sum()),
        T=("result", lambda s: (s == "T").sum()), PF=("points_for", "mean"), Games=("game_id", "count")).reset_index()
    vs = vs.assign(pct=(vs["W"] + 0.5 * vs["T"]) / vs["Games"]).sort_values(["pct", "Games"], ascending=[False, False])

    def bar(p):
        d = p - 0.5
        inner = (f'<div class="up" style="width:{abs(d) * 100:.0f}%"></div>' if d > 0
                 else f'<div class="down" style="width:{abs(d) * 100:.0f}%"></div>' if d < 0 else "")
        return f'<div class="bar"><div class="mid0"></div>{inner}</div>'

    html(html_table([("Opponent", "l"), ("Record", "r"), ("Win %", "r"), ("vs .500", "l")],
                    [[team(r.opponent_code), record(r.W, r.L, r.T), f"{r.pct:.3f}", bar(r.pct)] for r in vs.itertuples()],
                    compact=True))
    st.caption("Bar: blue = winning record against them, red = losing, centered on .500.")

with st.expander("Every game"):
    table(
        [{"Season": str(g.season), "Game": game_label(g._asdict()), "Opponent": g.opponent, "Result": g.result,
          "Score": None if pd.isna(g.points_for) else g.points_for,
          "Opp score": None if pd.isna(g.points_against) else g.points_against}
         for g in games.sort_values(["season", "week"], ascending=False).itertuples()],
        hide_index=True,
        column_config={"Score": st.column_config.NumberColumn(format="%.2f"),
                       "Opp score": st.column_config.NumberColumn(format="%.2f")},
    )
footer()
