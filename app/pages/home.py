"""
app/pages/home.py
Landing page: headline numbers, every champion, and the latest week's results.
"""

import pandas as pd
import streamlit as st

from lib import table, footer, full_name, query, record, team_games, text

st.title("National Fantasy League")
st.caption("League history since 2020: every game, every season, every champion.")

games = team_games()
standings = query("select * from core.v_season_standings")
bowls = query(
    """
    select season, team1_code, team2_code, team1_points, team2_points, winner_code, result_note
    from core.game where designation = 'Fantasy Bowl' order by season desc
    """
)
team_names = {(r.season, r.franchise_code): r.team_name for r in standings.itertuples()}

# --- headline numbers ---------------------------------------------------------
scored = games.dropna(subset=["points_for"])
best = scored.loc[scored["points_for"].idxmax()]
titles = standings[standings["champion"]].groupby("franchise_code").size().sort_values(ascending=False)
top_count = int(titles.iloc[0])
top_teams = ", ".join(full_name(c) for c in titles[titles == top_count].index)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Seasons", standings["season"].nunique(), border=True)
c2.metric("Games played", f"{games['game_id'].nunique():,}", border=True)
c3.metric("Highest score ever", f"{best['points_for']:.2f}", border=True)
c4.metric("Most titles", top_count, border=True)
st.caption(f"Highest score: {best['franchise']}, {best['season']} week {best['week']}. "
           f"Most titles: {top_teams}.")

# --- champions ------------------------------------------------------------------
st.subheader("Champions")
rows = []
for b in bowls.itertuples():
    points = {b.team1_code: b.team1_points, b.team2_code: b.team2_points}
    if pd.isna(b.winner_code):                       # 2022: co-champions
        champs = [b.team1_code, b.team2_code]
        rows.append({"Season": str(b.season), "Champion": " & ".join(full_name(c) for c in champs),
                     "Team name": " & ".join(text(team_names.get((b.season, c))) for c in champs),
                     "Runner-up": "(co-champions)", "Fantasy Bowl": "not finished"})
        continue
    loser = b.team2_code if b.winner_code == b.team1_code else b.team1_code
    score = ("" if pd.isna(points[b.winner_code]) or pd.isna(points[loser])
             else f"{points[b.winner_code]:.2f} - {points[loser]:.2f}")
    rows.append({"Season": str(b.season), "Champion": full_name(b.winner_code),
                 "Team name": text(team_names.get((b.season, b.winner_code))),
                 "Runner-up": full_name(loser), "Fantasy Bowl": score})
table(rows, hide_index=True)
if bowls["winner_code"].isna().any():
    st.caption("2022: Stonebrook and Great Lakes are co-champions. The Bills-Bengals game was cancelled after "
               "Damar Hamlin's injury, so the Fantasy Bowl couldn't be finished.")

# --- latest week ------------------------------------------------------------------
latest_season = int(games["season"].max())
latest_week = int(games.loc[games["season"] == latest_season, "week"].max())
left, right = st.columns([3, 2], gap="large")
with left:
    st.subheader(f"Latest results: {latest_season}, week {latest_week}")
    week = games[(games["season"] == latest_season) & (games["week"] == latest_week) & (games["result"] != "L")]
    week = week.sort_values("points_for", ascending=False).drop_duplicates("game_id")
    table(
        [{"Winner": g.franchise, "Score": f"{g.points_for:.2f} - {g.points_against:.2f}", "Loser": g.opponent}
         for g in week.itertuples()],
        hide_index=True,
    )
with right:
    st.subheader(f"{latest_season} standings")
    current = standings[standings["season"] == latest_season].sort_values("regular_season_rank")
    table(
        [{"Rank": int(r.regular_season_rank), "Franchise": r.franchise, "Record": record(r.wins, r.losses, r.ties),
          "Points for": r.points_for} for r in current.itertuples()],
        hide_index=True, column_config={"Points for": st.column_config.NumberColumn(format="%.2f")},
    )
    st.caption("Ranked by record, then points for.")

footer()
