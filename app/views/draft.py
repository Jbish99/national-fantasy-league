"""
app/views/draft.py
Draft value: what each pick produced, compared with the rest of its round.
"""

import altair as alt
import pandas as pd
import streamlit as st

from lib import DIVERGING, footer, full_name, query, table

st.title("Draft")
st.caption("Season points = a player's regular-season fantasy points under that year's league scoring, rebuilt from "
           "his real stat lines (weeks on anyone's roster count, and a player hurt all year counts as 0). "
           "Raw points favor QBs, so each player is measured against a replacement-level starter at his position "
           "(e.g. the 12th-best QB, 34th-best RB). Value = points over replacement minus the round's average.")

dv = query("select * from core.v_draft_value where season >= 2021")
if dv.empty:
    st.info("No draft data yet.")
    st.stop()
dv["team"] = dv["franchise_code"].map(full_name)
seasons = sorted(dv["season"].unique(), reverse=True)
choice = st.selectbox("Draft", [int(s) for s in seasons])
show_keepers = st.toggle("Include keepers", value=False)
df = dv[dv["season"] == choice]
if not show_keepers:
    df = df[~df["is_keeper"]]
valued = df.dropna(subset=["value_over_round"])

number = st.column_config.NumberColumn(format="%.2f")
plus = st.column_config.NumberColumn(format="%+.2f")
started = st.column_config.NumberColumn(format="%.2f", help="Points he scored as a starter for the team that drafted him")

# --- team draft grades ------------------------------------------------------------------------
st.subheader("Draft value by team")
teams = valued.groupby("team").agg(picks=("pick_no", "count"), value=("value_over_round", "sum"),
                                   started=("points_started_for_drafter", "sum")).reset_index()
teams = teams.sort_values("value", ascending=False)
limit = max(abs(teams["value"]).max(), 1) if len(teams) else 1
chart = alt.Chart(teams).mark_bar(cornerRadiusEnd=4, height={"band": 0.7}).encode(
    x=alt.X("value:Q", title="Total value vs round average (points over replacement)",
            scale=alt.Scale(domain=[-limit, limit])),
    y=alt.Y("team:N", sort="-x", title=None),
    color=alt.Color("value:Q", scale=alt.Scale(domain=[-limit, 0, limit], range=[DIVERGING[0], "#c3c2b7", DIVERGING[2]],
                                               interpolate="rgb"), legend=None),
    tooltip=[alt.Tooltip("team:N", title="Team"), alt.Tooltip("value:Q", title="Value", format="+.1f"),
             alt.Tooltip("picks:Q", title="Picks valued"),
             alt.Tooltip("started:Q", title="Points as starters for this team", format=".1f")],
)
rule = alt.Chart().mark_rule(color="#52514e").encode(x=alt.datum(0))
st.altair_chart((chart + rule).properties(height=30 * max(len(teams), 1)), width="stretch")

# --- best and worst picks -----------------------------------------------------------------------
def pick_rows(rows):
    return [{"Round": int(r.round), "Pick": int(r.pick_no), "Player": r.player_name, "Pos": r.position or "",
             "Team": r.team, "Season points": r.season_points,
             "Measured vs": "" if pd.isna(r.replacement_rank) else f"{r.position}{int(r.replacement_rank)}",
             "Over replacement": r.points_over_replacement, "Round avg": r.round_average,
             "Value": r.value_over_round, "Started": r.points_started_for_drafter}
            for r in rows.itertuples()]


config = {"Season points": number, "Over replacement": plus, "Round avg": plus, "Value": plus,
          "Started": started}
st.subheader("Steals")
table(pick_rows(valued.sort_values("value_over_round", ascending=False).head(10)), column_config=config)
st.subheader("Busts")
table(pick_rows(valued.sort_values("value_over_round").head(10)), column_config=config)

with st.expander("Every pick"):
    table(pick_rows(df.sort_values("pick_no")), column_config=config)
unvalued = df["value_over_round"].isna().sum()
if unvalued:
    st.caption(f"{unvalued} pick(s) have no value: defenses (no rebuilt points yet) or players not linked to NFL stats.")
footer()
