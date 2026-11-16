"""
app/views/standings.py
All-time standings, or one season's standings with final places.
"""

import pandas as pd
import streamlit as st

from lib import chip, esc, footer, html, html_table, ordinal, page_header, pts, query, record, team, text

page_header("Standings")

view = st.segmented_control("Show", ["All-time", "One season"], default="All-time", required=True)

if view == "All-time":
    df = query("select * from core.v_all_time_standings order by win_pct desc, points_for desc")
    rows = []
    for r in df.itertuples():
        years = f"{r.first_season}" + (f"-{r.last_season}" if r.last_season != r.first_season else "")
        rows.append([
            team(r.franchise_code, name="full"), f'{int(r.seasons)} <span class="muted small">({years})</span>',
            record(r.wins, r.losses, r.ties), f"{r.win_pct:.3f}", pts(r.points_for), pts(r.points_against),
            f"{r.points_per_game:.2f}",
            f'{int(r.playoff_appearances)} <span class="muted small">({record(r.playoff_wins, r.playoff_losses)})</span>',
            chip(str(int(r.championships))) if r.championships else '<span class="muted">0</span>',
            str(int(r.runner_ups)), ordinal(r.best_finish),
        ])
    html(html_table([("Franchise", "l"), ("Seasons", "r"), ("Record", "r"), ("Win %", "r"), ("Points for", "r"),
                     ("Points against", "r"), ("Pts / game", "r"), ("Playoffs (record)", "r"), ("Titles", "r"),
                     ("Runner-up", "r"), ("Best finish", "r")], rows))
    st.caption("Record, points and win % are regular season only. Titles include the 2022 co-championship.")
else:
    seasons = query("select season, status from core.season order by season desc")
    season = st.selectbox("Season", seasons["season"].tolist())
    status = seasons.loc[seasons["season"] == season, "status"].iloc[0]
    df = query("select * from core.v_season_standings where season = %s", (int(season),))
    finished = df["final_place"].notna().all()
    df = df.sort_values(["final_place", "regular_season_rank"] if finished else ["regular_season_rank"])
    rows = []
    for r in df.itertuples():
        if r.champion:
            playoffs = chip("Champion")
        elif r.made_playoffs:
            playoffs = record(r.playoff_wins, r.playoff_losses)
        else:
            playoffs = '<span class="muted">-</span>'
        place = ordinal(r.final_place if finished else r.regular_season_rank)
        rows.append([f'<span class="yr">{place}</span>', team(r.franchise_code, int(season)), esc(text(r.team_name)),
                     esc(text(r.owner)), record(r.wins, r.losses, r.ties), f"{r.win_pct:.3f}", pts(r.points_for, season),
                     pts(r.points_against, season), f"{r.points_per_game:.2f}", ordinal(r.regular_season_rank), playoffs])
    html(html_table([("Final place" if finished else "Rank", "l"), ("Franchise", "l"), ("Team name", "l"),
                     ("Owner", "l"), ("Record", "r"), ("Win %", "r"), ("Points for", "r"), ("Points against", "r"),
                     ("Pts / game", "r"), ("Reg. season", "r"), ("Playoffs", "r")], rows))
    if not finished:
        st.caption(f"{season} is {'in progress' if status != 'complete' else 'missing placement games'}: "
                   "ranked by record, then points for.")
    else:
        st.caption("Final place comes from the placement games (Fantasy Bowl, 3rd Place Bowl...). "
                   "Regular-season rank orders by record, then points for; the league's real tiebreakers may differ.")
footer()
