"""
app/views/positions.py
Positional strengths and weaknesses: starters' points per week by position vs the league average.
Phase 7a: weather by position (how QBs, RBs, WRs, TEs and kickers score in a dome, mild weather, cold and wind).
"""

import altair as alt
import psycopg
import streamlit as st

from lib import (BG, DIVERGING, TEXT, WEATHER, footer, full_name, html, html_table, mix, page_header, query, section,
                 short_name, show_chart, signed, text_on)

page_header("Positions", "Each team's starters' points per regular-season week at each position (a FLEX starter "
                         "counts at his own position), compared with the league average that season.")

ps = query("select * from core.v_position_strength where position <> '?'")
if ps.empty:
    st.info("No lineup data yet.")
    st.stop()
seasons = sorted(ps["season"].unique(), reverse=True)
left, _ = st.columns([1, 3])
choice = left.selectbox("Season", [int(s) for s in seasons])
df = ps[ps["season"] == choice].copy()
df["team"] = df["franchise_code"].map(short_name)
df["team_full"] = df["franchise_code"].map(full_name)
order = ["QB", "RB", "WR", "TE", "K", "DEF"]
positions = [p for p in order if p in set(df["position"])] + sorted(set(df["position"]) - set(order))
teams = sorted(df["team"].unique())

limit = max(abs(df["vs_league"]).max(), 1)
df["ink"] = [text_on(mix(DIVERGING, (v + limit) / (2 * limit))) for v in df["vs_league"]]
section("Points per week vs the league average")
x = alt.X("position:N", sort=positions, title=None, axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False))
y = alt.Y("team:N", sort=teams, title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=160))
heat = alt.Chart(df).mark_rect(stroke=BG, strokeWidth=2, cornerRadius=3).encode(
    x=x, y=y,
    color=alt.Color("vs_league:Q", scale=alt.Scale(domain=[-limit, 0, limit], range=DIVERGING, interpolate="rgb"),
                    legend=alt.Legend(title="Points per week vs league average", orient="bottom", gradientLength=240,
                                      titleLimit=400)),
    tooltip=[alt.Tooltip("team_full:N", title="Team"), alt.Tooltip("position:N", title="Position"),
             alt.Tooltip("points_per_week:Q", title="Points per week", format=".2f"),
             alt.Tooltip("league_average:Q", title="League average", format=".2f"),
             alt.Tooltip("vs_league:Q", title="Difference", format="+.2f")],
)
labels = alt.Chart(df).mark_text(fontSize=12, fontWeight=600).encode(   # ink: whichever reads better on the cell
    x=x, y=y, text=alt.Text("vs_league:Q", format="+.1f"),
    color=alt.condition(alt.datum.ink == BG, alt.value(BG), alt.value(TEXT)))
show_chart((heat + labels).properties(height=34 * len(teams)))
st.caption("Blue = above the league average at that position, red = below.")

with st.expander("Show as a table"):
    grid = df.pivot(index="team_full", columns="position", values="points_per_week").reindex(columns=positions)
    avg = df.groupby("position")["league_average"].first().reindex(positions)
    grid.loc["League average"] = avg
    st.dataframe(grid.reset_index().rename(columns={"team_full": "Team"}), hide_index=True,
                 column_config={p: st.column_config.NumberColumn(format="%.2f") for p in positions},
                 height=35 * (len(grid) + 1) + 3)

# --- weather by position ---------------------------------------------------------------------
section("Weather by position", "Points per game vs the player's own season average, by conditions")
try:
    wx = query("select * from core.v_position_conditions")
except psycopg.Error:
    wx = None
    st.info("Weather by position needs one database update: run `python -m etl.run_sql --all`, then reload.")
if wx is not None and not wx.empty:
    wx_seasons = sorted(wx["season"].dropna().astype(int).unique(), reverse=True)
    span = st.segmented_control("Seasons", ["All seasons"] + [str(s) for s in wx_seasons], default="All seasons",
                                required=True, label_visibility="collapsed", key="weather_seasons")
    w = wx[wx["season"].isna()] if span == "All seasons" else wx[wx["season"] == int(span)]
    w = w.copy()
    conditions_order = [s for _, _, s, _ in WEATHER]
    w_positions = [p for p in order if p in set(w["position"])]
    wlimit = max(float(w["vs_season_avg"].abs().max()), 1.0)
    w["ink"] = [text_on(mix(DIVERGING, (float(v) + wlimit) / (2 * wlimit))) for v in w["vs_season_avg"]]
    wx_x = alt.X("condition:N", sort=conditions_order, title=None,
                 axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False))
    wx_y = alt.Y("position:N", sort=w_positions, title=None, axis=alt.Axis(ticks=False, domain=False))
    cells = alt.Chart(w).mark_rect(stroke=BG, strokeWidth=2, cornerRadius=3).encode(
        x=wx_x, y=wx_y,
        color=alt.Color("vs_season_avg:Q", scale=alt.Scale(domain=[-wlimit, 0, wlimit], range=DIVERGING,
                                                           interpolate="rgb"),
                        legend=alt.Legend(title="Points per game vs the player's season average", orient="bottom",
                                          gradientLength=240, titleLimit=400)),
        tooltip=[alt.Tooltip("position:N", title="Position"), alt.Tooltip("condition:N", title="Conditions"),
                 alt.Tooltip("vs_season_avg:Q", title="vs season average", format="+.2f"),
                 alt.Tooltip("points_per_game:Q", title="Points per game", format=".2f"),
                 alt.Tooltip("games:Q", title="Games", format=",")],
    )
    cell_text = alt.Chart(w).mark_text(fontSize=12, fontWeight=600).encode(
        x=wx_x, y=wx_y, text=alt.Text("vs_season_avg:Q", format="+.1f"),
        color=alt.condition(alt.datum.ink == BG, alt.value(BG), alt.value(TEXT)))
    show_chart((cells + cell_text).properties(height=56 * len(w_positions)))
    st.caption("Players who were in a league starting lineup at least once that season, all their NFL "
               "regular-season games that season (2021 on), scored with our league's rules. Each game is compared "
               "with that player's average for the season, so star players and the full-to-half PPR switch don't "
               "skew it. Cold = 40°F or colder, Windy = 15+ mph (a cold, windy game counts in both), Dome includes "
               "closed roofs. Kickers start in 2026. Defenses come with the DEF points rebuild (Phase 7b).")
    with st.expander("Show as a table"):
        body = []
        for pos in w_positions:
            row = [f"<b>{pos}</b>"]
            for _, _, cond, _ in WEATHER:
                hit = w[(w["position"] == pos) & (w["condition"] == cond)]
                if hit.empty:
                    row.append('<span class="muted">-</span>')
                else:
                    h = hit.iloc[0]
                    row.append(f'{signed(h["vs_season_avg"])}<div class="muted small">{h["points_per_game"]:.2f} '
                               f'per game, {int(h["games"]):,} games</div>')
            body.append(row)
        html(html_table([("Position", "l")] + [(c, "r") for _, _, c, _ in WEATHER], body, compact=True))
footer()
