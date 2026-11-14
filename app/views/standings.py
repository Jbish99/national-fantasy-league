"""
app/views/standings.py
All-time standings, or one season's standings with final places.
"""

import streamlit as st

from lib import table, footer, ordinal, query, record, text

st.title("Standings")

view = st.segmented_control("Show", ["All-time", "One season"], default="All-time", required=True)

if view == "All-time":
    df = query("select * from core.v_all_time_standings order by win_pct desc, points_for desc")
    shown = df.assign(
        Record=[record(r.wins, r.losses, r.ties) for r in df.itertuples()],
        Playoffs=[f"{r.playoff_appearances} ({record(r.playoff_wins, r.playoff_losses)})" for r in df.itertuples()],
        Seasons=[f"{r.seasons} ({r.first_season}" + (f"-{r.last_season})" if r.last_season != r.first_season else ")")
                 for r in df.itertuples()],
        Best=[ordinal(x) for x in df["best_finish"]],
    )[["franchise", "Seasons", "Record", "win_pct", "points_for", "points_against", "points_per_game",
       "Playoffs", "championships", "runner_ups", "Best"]]
    table(
        shown,
        hide_index=True,
        column_config={
            "franchise": "Franchise",
            "win_pct": st.column_config.NumberColumn("Win %", format="%.3f"),
            "points_for": st.column_config.NumberColumn("Points for", format="%.2f"),
            "points_against": st.column_config.NumberColumn("Points against", format="%.2f"),
            "points_per_game": st.column_config.NumberColumn("Pts / game", format="%.2f"),
            "Playoffs": st.column_config.TextColumn("Playoffs (record)"),
            "championships": "Titles",
            "runner_ups": "Runner-up",
            "Best": "Best finish",
        },
    )
    st.caption("Record, points and win % are regular season only. Titles include the 2022 co-championship.")
else:
    seasons = query("select season, status from core.season order by season desc")
    season = st.selectbox("Season", seasons["season"].tolist())
    status = seasons.loc[seasons["season"] == season, "status"].iloc[0]
    df = query("select * from core.v_season_standings where season = %s", (int(season),))
    finished = df["final_place"].notna().all()
    df = df.sort_values(["final_place", "regular_season_rank"] if finished else ["regular_season_rank"])
    shown = df.assign(
        Place=[ordinal(x) for x in (df["final_place"] if finished else df["regular_season_rank"])],
        Record=[record(r.wins, r.losses, r.ties) for r in df.itertuples()],
        Playoffs=["Champion" if r.champion else (record(r.playoff_wins, r.playoff_losses) if r.made_playoffs else "-")
                  for r in df.itertuples()],
        Seed=[ordinal(x) for x in df["regular_season_rank"]],
        team_name=[text(v) for v in df["team_name"]], owner=[text(v) for v in df["owner"]],
    )[["Place", "franchise", "team_name", "owner", "Record", "win_pct", "points_for", "points_against",
       "points_per_game", "Seed", "Playoffs"]]
    table(
        shown,
        hide_index=True,
        column_config={
            "Place": "Final place" if finished else "Rank",
            "franchise": "Franchise",
            "team_name": "Team name",
            "owner": "Owner",
            "win_pct": st.column_config.NumberColumn("Win %", format="%.3f"),
            "points_for": st.column_config.NumberColumn("Points for", format="%.2f"),
            "points_against": st.column_config.NumberColumn("Points against", format="%.2f"),
            "points_per_game": st.column_config.NumberColumn("Pts / game", format="%.2f"),
            "Seed": "Regular-season rank",
            "Playoffs": "Playoff record",
        },
    )
    if not finished:
        st.caption(f"{season} is {'in progress' if status != 'complete' else 'missing placement games'}: "
                   "ranked by record, then points for.")
    else:
        st.caption("Final place comes from the placement games (Fantasy Bowl, 3rd Place Bowl...). "
                   "Regular-season rank orders by record, then points for; the league's real tiebreakers may differ.")
footer()
