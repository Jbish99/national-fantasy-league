"""
app/views/draft.py
Draft value: what each pick produced, compared with the rest of its round.
"""

import altair as alt
import pandas as pd
import streamlit as st

from lib import (DIVERGING, MUTED, esc, footer, full_name, html, html_table, page_header, position_chip, query,
                 section, show_chart, table, team)

page_header("Draft", "Season points = a player's regular-season fantasy points under that year's league scoring, "
                     "rebuilt from his real stat lines (a player hurt all year counts as 0). Raw points favor QBs, so "
                     "each player is measured against a replacement-level starter at his position (e.g. the 12th-best "
                     "QB, 34th-best RB). Value = points over replacement minus the round's average.")

dv = query("select * from core.v_draft_value where season >= 2021")
if dv.empty:
    st.info("No draft data yet.")
    st.stop()
dv["team"] = dv["franchise_code"].map(full_name)
seasons = sorted(dv["season"].unique(), reverse=True)
left, right, _ = st.columns([1, 1, 2])
choice = left.selectbox("Draft", [int(s) for s in seasons])
with right:
    st.write("")
    show_keepers = st.toggle("Include keepers", value=False)
df = dv[dv["season"] == choice]
if not show_keepers:
    df = df[~df["is_keeper"]]
valued = df.dropna(subset=["value_over_round"])

# --- team draft grades ------------------------------------------------------------------------
section("Draft value by team")
teams = valued.groupby("team").agg(picks=("pick_no", "count"), value=("value_over_round", "sum"),
                                   started=("points_started_for_drafter", "sum")).reset_index()
teams = teams.sort_values("value", ascending=False)
limit = max(abs(teams["value"]).max(), 1) if len(teams) else 1
chart = alt.Chart(teams).mark_bar(cornerRadiusEnd=4, height={"band": 0.7}).encode(
    x=alt.X("value:Q", title="Total value vs round average (points over replacement)",
            scale=alt.Scale(domain=[-limit, limit])),
    y=alt.Y("team:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
    color=alt.Color("value:Q", scale=alt.Scale(domain=[-limit, 0, limit], range=[DIVERGING[0], MUTED, DIVERGING[2]],
                                               interpolate="rgb"), legend=None),
    tooltip=[alt.Tooltip("team:N", title="Team"), alt.Tooltip("value:Q", title="Value", format="+.1f"),
             alt.Tooltip("picks:Q", title="Picks valued"),
             alt.Tooltip("started:Q", title="Points as starters for this team", format=".1f")],
)
rule = alt.Chart().mark_rule(color=MUTED).encode(x=alt.datum(0))
show_chart((chart + rule).properties(height=30 * max(len(teams), 1)))
st.caption("Blue = drafted better than the round average; red = worse.")


# --- best and worst picks -----------------------------------------------------------------------
def signed(value):
    if pd.isna(value):
        return ""
    cls = "pos" if value > 0.005 else "neg" if value < -0.005 else "muted"
    return f'<span class="{cls}">{value:+.2f}</span>'


def pick_table(rows):
    return html_table(
        [("Round", "r"), ("Pick", "r"), ("Player", "l"), ("Pos", "l"), ("Team", "l"), ("Season points", "r"),
         ("Measured vs", "l"), ("Over replacement", "r"), ("Round avg", "r"), ("Value", "r"), ("Started", "r")],
        [[str(int(r.round)), str(int(r.pick_no)), f"<strong>{esc(r.player_name)}</strong>", position_chip(r.position),
          team(r.franchise_code, r.season), "" if pd.isna(r.season_points) else f"{r.season_points:.2f}",
          "" if pd.isna(r.replacement_rank) else f'<span class="muted">{r.position}{int(r.replacement_rank)}</span>',
          signed(r.points_over_replacement), signed(r.round_average),
          f'<span class="big-n">{signed(r.value_over_round)}</span>',
          "" if pd.isna(r.points_started_for_drafter) else f"{r.points_started_for_drafter:.2f}"]
         for r in rows.itertuples()])


section("Steals")
html(pick_table(valued.sort_values("value_over_round", ascending=False).head(10)))
section("Busts")
html(pick_table(valued.sort_values("value_over_round").head(10)))
st.caption("Started = points he scored as a starter for the team that drafted him.")

with st.expander("Every pick"):
    number, plus = st.column_config.NumberColumn(format="%.2f"), st.column_config.NumberColumn(format="%+.2f")
    table([{"Round": int(r.round), "Pick": int(r.pick_no), "Player": r.player_name, "Pos": r.position or "",
             "Team": r.team, "Season points": r.season_points,
             "Measured vs": "" if pd.isna(r.replacement_rank) else f"{r.position}{int(r.replacement_rank)}",
             "Over replacement": r.points_over_replacement, "Round avg": r.round_average,
             "Value": r.value_over_round, "Started": r.points_started_for_drafter}
            for r in df.sort_values("pick_no").itertuples()],
          column_config={"Season points": number, "Over replacement": plus, "Round avg": plus, "Value": plus,
                         "Started": number})
unvalued = df["value_over_round"].isna().sum()
if unvalued:
    st.caption(f"{unvalued} pick(s) have no value: defenses (no rebuilt points yet) or players not linked to NFL stats.")
footer()
