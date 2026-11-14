"""
app/views/head_to_head.py
Head-to-head matrix for every pair of franchises, and the full series between any two.
"""

import altair as alt
import pandas as pd
import streamlit as st

from lib import DIVERGING, footer, full_name, game_label, record, short_name, table, team_games

st.title("Head-to-head")

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
codes = sorted(set(games["franchise_code"]), key=short_name)
rows = [short_name(c) for c in codes]

st.subheader("Matrix")
st.caption("Read across: each row is that team's record against the team in the column (league codes). "
           "Blue = winning record, red = losing record, gray = even. Hover a cell for details.")
x = alt.X("Opp:N", sort=codes, title=None, axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False))
y = alt.Y("Team:N", sort=rows, title=None, axis=alt.Axis(ticks=False, domain=False))
heat = alt.Chart(cells).mark_rect(stroke="white", strokeWidth=2, cornerRadius=2).encode(
    x=x, y=y,
    color=alt.Color("Win %:Q", scale=alt.Scale(domain=[0, 0.5, 1], range=DIVERGING, interpolate="rgb"),
                    legend=alt.Legend(title="Win %", format=".0%", orient="bottom", gradientLength=240)),
    tooltip=[alt.Tooltip("Team name:N", title="Team"), alt.Tooltip("Opponent:N"), alt.Tooltip("Record:N"),
             alt.Tooltip("Games:Q"), alt.Tooltip("Win %:Q", format=".3f")],
)
labels = alt.Chart(cells).mark_text(fontSize=12, color="#0b0b0b").encode(x=x, y=y, text="Record:N")
st.altair_chart((heat + labels).properties(height=34 * len(rows)), width="stretch")

with st.expander("Show as a table"):
    grid = cells.pivot(index="Team", columns="Opp", values="Record").reindex(index=rows, columns=codes).fillna("")
    for code in codes:
        grid.loc[short_name(code), code] = "-"
    st.dataframe(grid, height=35 * (len(rows) + 1) + 3)

# --- one series -----------------------------------------------------------------------
st.subheader("Series history")
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
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"{short_name(team_a)} record", record(w, l, t), border=True)
    m2.metric("Games", len(series), border=True)
    m3.metric(f"{short_name(team_a)} points / game", f"{scored['points_for'].mean():.2f}" if len(scored) else "-",
              border=True)
    m4.metric(f"{short_name(team_b)} points / game", f"{scored['points_against'].mean():.2f}" if len(scored) else "-",
              border=True)
    last = series.iloc[0]
    winner = {"W": full_name(team_a), "L": full_name(team_b)}.get(last["result"])
    st.caption(f"Last meeting: {last['season']}, {game_label(last)}: "
               + (f"won by {winner}." if winner else "a tie (co-champions)."))
    number = st.column_config.NumberColumn(format="%.2f")
    table(
        [{"Season": str(g.season), "Game": game_label(g._asdict()),
          short_name(team_a): None if pd.isna(g.points_for) else g.points_for,
          short_name(team_b): None if pd.isna(g.points_against) else g.points_against,
          "Winner": {"W": full_name(team_a), "L": full_name(team_b)}.get(g.result, "Tie (co-champions)"),
          "Margin": None if pd.isna(g.margin) else abs(g.margin)}
         for g in series.itertuples()],
        column_config={short_name(team_a): number, short_name(team_b): number, "Margin": number},
    )
footer()
