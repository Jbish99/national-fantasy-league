"""
app/views/positions.py
Positional strengths and weaknesses: starters' points per week by position vs the league average.
"""

import altair as alt
import streamlit as st

from lib import DIVERGING, footer, full_name, query, short_name

st.title("Positions")
st.caption("Each team's starters' points per regular-season week at each position (a FLEX starter counts at his "
           "own position), compared with the league average that season. Blue = above average, red = below.")

ps = query("select * from core.v_position_strength where position <> '?'")
if ps.empty:
    st.info("No lineup data yet.")
    st.stop()
seasons = sorted(ps["season"].unique(), reverse=True)
choice = st.selectbox("Season", [int(s) for s in seasons])
df = ps[ps["season"] == choice].copy()
df["team"] = df["franchise_code"].map(short_name)
df["team_full"] = df["franchise_code"].map(full_name)
order = ["QB", "RB", "WR", "TE", "K", "DEF"]
positions = [p for p in order if p in set(df["position"])] + sorted(set(df["position"]) - set(order))
teams = sorted(df["team"].unique())

limit = max(abs(df["vs_league"]).max(), 1)
x = alt.X("position:N", sort=positions, title=None, axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False))
y = alt.Y("team:N", sort=teams, title=None, axis=alt.Axis(ticks=False, domain=False))
heat = alt.Chart(df).mark_rect(stroke="white", strokeWidth=2, cornerRadius=2).encode(
    x=x, y=y,
    color=alt.Color("vs_league:Q", scale=alt.Scale(domain=[-limit, 0, limit], range=DIVERGING, interpolate="rgb"),
                    legend=alt.Legend(title="Points per week vs league average", orient="bottom", gradientLength=240,
                                      titleLimit=400)),
    tooltip=[alt.Tooltip("team_full:N", title="Team"), alt.Tooltip("position:N", title="Position"),
             alt.Tooltip("points_per_week:Q", title="Points per week", format=".2f"),
             alt.Tooltip("league_average:Q", title="League average", format=".2f"),
             alt.Tooltip("vs_league:Q", title="Difference", format="+.2f")],
)
labels = alt.Chart(df).mark_text(fontSize=12, color="#0b0b0b").encode(
    x=x, y=y, text=alt.Text("vs_league:Q", format="+.1f"))
st.altair_chart((heat + labels).properties(height=34 * len(teams)), width="stretch")

with st.expander("Show as a table"):
    grid = df.pivot(index="team_full", columns="position", values="points_per_week").reindex(columns=positions)
    avg = df.groupby("position")["league_average"].first().reindex(positions)
    grid.loc["League average"] = avg
    st.dataframe(grid.reset_index().rename(columns={"team_full": "Team"}), hide_index=True,
                 column_config={p: st.column_config.NumberColumn(format="%.2f") for p in positions},
                 height=35 * (len(grid) + 1) + 3)
footer()
