"""
app/views/champions.py
Titles by franchise, and each season's playoff and placement games.
"""

import pandas as pd
import streamlit as st

from lib import table, footer, ordinal, query, team_games, text

st.title("Champions & playoffs")

standings = query("select * from core.v_season_standings")
alltime = query("select * from core.v_all_time_standings")

# --- titles by franchise -------------------------------------------------------------
st.subheader("Titles and finishes")
counts = alltime[["franchise_code", "franchise", "championships", "runner_ups", "playoff_appearances",
                  "best_finish", "avg_finish"]].copy()
years = (standings[standings["champion"]].groupby("franchise_code")["season"]
         .apply(lambda s: ", ".join(str(x) for x in sorted(s))))
counts["Title years"] = counts["franchise_code"].map(years).fillna("")
counts = counts.sort_values(["championships", "runner_ups", "avg_finish"], ascending=[False, False, True])
table(
    counts.drop(columns="franchise_code").assign(best_finish=[ordinal(x) for x in counts["best_finish"]]),
    hide_index=True,
    column_config={"franchise": "Franchise", "championships": "Titles", "runner_ups": "Runner-up",
                   "playoff_appearances": "Playoff trips", "best_finish": "Best finish",
                   "avg_finish": st.column_config.NumberColumn("Avg finish", format="%.1f")},
)

# --- one season's postseason ------------------------------------------------------------
st.subheader("Postseason by season")
games = team_games()
post_seasons = sorted(games.loc[games["game_type"] != "regular", "season"].unique(), reverse=True)
season = st.selectbox("Season", post_seasons)
post = games[(games["season"] == season) & (games["game_type"] != "regular") & (games["result"] != "L")]
post = post.drop_duplicates("game_id").sort_values(["game_type", "week", "designation"], ascending=[False, True, True])

for game_type, title in (("playoff", "Playoffs"), ("consolation", "Consolation")):
    part = post[post["game_type"] == game_type]
    if part.empty:
        continue
    st.markdown(f"**{title}**")
    table(
        [{"Week": int(g.week), "Game": g.designation or "",
          "Winner": g.franchise if g.result == "W" else f"{g.franchise} (tie)",
          "Score": "" if pd.isna(g.points_for) else f"{g.points_for:.2f} - {g.points_against:.2f}",
          "Loser": g.opponent}
         for g in part.itertuples()],
    )
if season == 2020:
    st.caption("Some 2020 consolation games have only a recorded winner, so no score is shown.")

st.markdown("**Final standings**")
final = standings[standings["season"] == season].sort_values(["final_place", "regular_season_rank"])
left, _ = st.columns([3, 2])
table(
    [{"Place": ordinal(r.final_place), "Franchise": r.franchise, "Team name": text(r.team_name)}
     for r in final.itertuples()],
    container=left,
)
footer()
