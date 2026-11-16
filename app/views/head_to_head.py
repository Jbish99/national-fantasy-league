"""
app/views/head_to_head.py
Head-to-head matrix for every pair of franchises, and the full series between any two:
the series score, their biggest meeting as a matchup card, and every game.
"""

import altair as alt
import pandas as pd
import streamlit as st

from lib import (BG, DIVERGING, TEXT, box_url, chip, city, esc, footer, full_name, game_label, hero, html, html_table,
                 link, logo, mix, ordinal, page_header, pts, query, record, section, short_name, show_chart, team,
                 team_games, text_on)

page_header("Head-to-head")

all_games = team_games()
kind = st.segmented_control("Games", ["All games", "Regular season", "Postseason"], default="All games",
                            required=True)
games = all_games
if kind == "Regular season":
    games = all_games[all_games["game_type"] == "regular"]
elif kind == "Postseason":
    games = all_games[all_games["game_type"] != "regular"]

# --- the matrix (a heatmap that fits any screen width) ------------------------------------
pairs = games.groupby(["franchise_code", "opponent_code"])["result"].value_counts().unstack(fill_value=0)
for col in "WLT":
    if col not in pairs:
        pairs[col] = 0
cells = pd.DataFrame([
    {"Team": short_name(me), "Opp": opp, "Team name": full_name(me), "Opponent": full_name(opp),
     "Record": record(r["W"], r["L"], r["T"]), "Games": int(r["W"] + r["L"] + r["T"]),
     "Win %": (r["W"] + 0.5 * r["T"]) / (r["W"] + r["L"] + r["T"])}
    for (me, opp), r in pairs.iterrows()
])
cells["ink"] = [text_on(mix(DIVERGING, p)) for p in cells["Win %"]]
codes = sorted(set(games["franchise_code"]), key=short_name)
rows = [short_name(c) for c in codes]

section("Matrix", "Read across: each row's record against the team in the column")
x = alt.X("Opp:N", sort=codes, title=None, axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False))
y = alt.Y("Team:N", sort=rows, title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=160))
heat = alt.Chart(cells).mark_rect(stroke=BG, strokeWidth=2, cornerRadius=3).encode(
    x=x, y=y,
    color=alt.Color("Win %:Q", scale=alt.Scale(domain=[0, 0.5, 1], range=DIVERGING, interpolate="rgb"),
                    legend=alt.Legend(title="Win %", format=".0%", orient="bottom", gradientLength=240)),
    tooltip=[alt.Tooltip("Team name:N", title="Team"), alt.Tooltip("Opponent:N"), alt.Tooltip("Record:N"),
             alt.Tooltip("Games:Q"), alt.Tooltip("Win %:Q", format=".3f")],
)
labels = alt.Chart(cells).mark_text(fontSize=12, fontWeight=600).encode(   # ink: whichever reads better on the cell
    x=x, y=y, text="Record:N", color=alt.condition(alt.datum.ink == BG, alt.value(BG), alt.value(TEXT)))
show_chart((heat + labels).properties(height=34 * len(rows)))
st.caption("Blue = winning record, red = losing record, gray = even. Hover a cell for details.")

with st.expander("Show as a table"):
    grid = cells.pivot(index="Team", columns="Opp", values="Record").reindex(index=rows, columns=codes).fillna("")
    for code in codes:
        grid.loc[short_name(code), code] = "-"
    st.dataframe(grid, height=35 * (len(rows) + 1) + 3)

# --- one series -----------------------------------------------------------------------
section("Series history")
all_codes = sorted(set(all_games["franchise_code"]), key=full_name)
left, right = st.columns(2)
team_a = left.selectbox("Team", all_codes, format_func=full_name,
                        index=all_codes.index("DET") if "DET" in all_codes else 0)
others = [c for c in all_codes if c != team_a]
team_b = right.selectbox("Opponent", others, format_func=full_name,
                         index=others.index("OAK") if "OAK" in others else 0)

series = games[(games["franchise_code"] == team_a) & (games["opponent_code"] == team_b)].sort_values(
    ["season", "week"], ascending=False)
if series.empty:
    st.info(f"{full_name(team_a)} and {full_name(team_b)} haven't played"
            + ("" if kind == "All games" else f" in the {kind.lower()}") + ".")
else:
    w, l, t = (int((series["result"] == r).sum()) for r in "WLT")
    scored = series.dropna(subset=["points_for", "points_against"])
    if w > l:
        lead = f"{city(team_a)} leads"
    elif l > w:
        lead = f"{city(team_b)} leads"
    else:
        lead = "Series tied"
    ppg_a = f"{scored['points_for'].mean():.2f} points per game" if len(scored) else ""
    ppg_b = f"{scored['points_against'].mean():.2f} points per game" if len(scored) else ""
    first = int(series["season"].min())
    html(f"""<section class="card series" aria-label="Series summary">
<div class="s-l">{logo(team_a, size=56)}<div><div class="tname" style="font-size:22px">{esc(full_name(team_a))}</div>
<div class="muted small">{ppg_a}</div></div></div>
<div class="s-m"><div class="stat-v" style="font-size:56px">{record(w, l, t)}</div>
<div class="muted small">{lead}, {len(series)} game{"s" if len(series) != 1 else ""} since {first}</div></div>
<div class="s-r"><div><div class="tname" style="font-size:22px">{esc(full_name(team_b))}</div>
<div class="muted small">{ppg_b}</div></div>{logo(team_b, size=56)}</div>
</section>""")

    # the biggest meeting: the furthest postseason round they met in; otherwise their last game
    stage = {"Fantasy Bowl": 0, "2nd Round": 1, "AFC Championship": 1, "NFC Championship": 1, "1st Round": 2}

    def importance(g):
        if g["game_type"] == "playoff":
            return stage.get(g["designation"], 3)
        if g["game_type"] == "consolation":
            return 4
        return 5

    ranked = series.assign(rank=series.apply(importance, axis=1)).sort_values(["rank", "season", "week"],
                                                                             ascending=[True, False, False])
    big = ranked.iloc[0]
    postseason = big["rank"] < 5
    standings = query("select season, franchise_code, wins, losses, ties, regular_season_rank "
                      "from core.v_season_standings where season = %s", (int(big["season"]),))
    st_of = {r.franchise_code: r for r in standings.itertuples()}

    def season_line(code):
        r = st_of.get(code)
        if r is None:
            return ""
        return f"{record(r.wins, r.losses, r.ties)}, {ordinal(r.regular_season_rank)}" + (" seed" if postseason else "")

    winner = "auto"
    if big["result"] == "T":
        winner = None
    champion = big["designation"] == "Fantasy Bowl" and big["result"] in ("W", "L")
    section("Biggest meeting" if postseason else "Last meeting",
            "The furthest postseason round they met in" if postseason else None)
    html(hero({"code": team_a, "score": big["points_for"], "season": int(big["season"]), "record": season_line(team_a)},
              {"code": team_b, "score": big["points_against"], "season": int(big["season"]), "record": season_line(team_b)},
              label=game_label(big) if postseason else "", sublabel=f"{big['season']}, week {big['week']}",
              note_left=chip("Champion") if champion and big["result"] == "W" else "",
              note_right=chip("Champion") if champion and big["result"] == "L" else "",
              winner=winner))
    if big["result"] == "T" and big["designation"] == "Fantasy Bowl":
        st.caption("Co-champions: the Bills-Bengals game was cancelled, so this Fantasy Bowl couldn't be finished.")

    section("Every meeting", "Newest first")
    body = []
    for g in series.itertuples():
        won_a, won_b = g.result == "W", g.result == "L"
        tag = game_label(g._asdict())
        label = link(box_url(g.game_id), chip(tag) if g.game_type != "regular" else esc(tag))
        winner_cell = team(team_a) if won_a else team(team_b) if won_b else '<span class="muted">Tie (co-champions)</span>'
        body.append([f'<span class="yr">{g.season}</span>', label,
                     f'<span class="big-n{"" if won_a else " dim"}">{pts(g.points_for, g.season)}</span>',
                     f'<span class="big-n{"" if won_b else " dim"}">{pts(g.points_against, g.season)}</span>',
                     winner_cell, "" if pd.isna(g.margin) else f"{abs(g.margin):.2f}"])
    html(html_table([("Season", "l"), ("Game", "l"), (city(team_a), "r"), (city(team_b), "r"), ("Winner", "l"),
                     ("Margin", "r")], body))
footer()
