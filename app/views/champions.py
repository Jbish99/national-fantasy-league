"""
app/views/champions.py
Titles by franchise, and each season's postseason: the Fantasy Bowl as a matchup card, every playoff and
placement game, and the final standings.
"""

import pandas as pd
import streamlit as st

from lib import (chip, footer, html, html_table, hero, league_art, ordinal, page_header, query, record,
                 roman, score_tile, section, team, team_games, text, esc, tiles)

page_header("Champions & playoffs")

standings = query("select * from core.v_season_standings")
alltime = query("select * from core.v_all_time_standings")

# --- titles by franchise -------------------------------------------------------------
section("Titles and finishes")
years = (standings[standings["champion"]].groupby("franchise_code")["season"]
         .apply(lambda s: ", ".join(str(x) for x in sorted(s))))
counts = alltime.assign(years=alltime["franchise_code"].map(years).fillna(""))
counts = counts.sort_values(["championships", "runner_ups", "avg_finish"], ascending=[False, False, True])
html(html_table(
    [("Franchise", "l"), ("Titles", "r"), ("Title years", "l"), ("Runner-up", "r"), ("Playoff trips", "r"),
     ("Best finish", "r"), ("Avg finish", "r")],
    [[team(r.franchise_code, name="full"), chip(str(int(r.championships))) if r.championships else '<span class="muted">0</span>',
      esc(r.years), str(int(r.runner_ups)), str(int(r.playoff_appearances)), ordinal(r.best_finish),
      "" if pd.isna(r.avg_finish) else f"{r.avg_finish:.1f}"] for r in counts.itertuples()],
))

# --- one season's postseason ------------------------------------------------------------
section("Postseason by season")
games = team_games()
post_seasons = sorted(games.loc[games["game_type"] != "regular", "season"].unique(), reverse=True)
season = int(st.selectbox("Season", post_seasons))
post = games[(games["season"] == season) & (games["game_type"] != "regular")]
one_side = post.sort_values(["result", "points_for"], ascending=[False, False]).drop_duplicates("game_id")
seeds = standings[standings["season"] == season].set_index("franchise_code")


def seed_line(code):
    if code not in seeds.index:
        return ""
    r = seeds.loc[code]
    return f"{record(r.wins, r.losses, r.ties)}, {ordinal(r.regular_season_rank)} seed"


bowl = one_side[one_side["designation"] == "Fantasy Bowl"]
if len(bowl):
    b = bowl.iloc[0]
    co = b["result"] == "T"
    bowl_logo = league_art(kind="special_logo", season=season)
    html(hero({"code": b["franchise_code"], "score": b["points_for"], "season": season, "record": seed_line(b["franchise_code"])},
              {"code": b["opponent_code"], "score": b["points_against"], "season": season, "record": seed_line(b["opponent_code"])},
              label=f"Fantasy Bowl {roman(season - 2019)}", sublabel=f"{season}, week {b['week']}",
              note_left=chip("Co-champion" if co else "Champion"), note_right=chip("Co-champion") if co else "",
              winner=None if co else "auto"))
    if co:
        st.caption("Co-champions: the Bills-Bengals game was cancelled after Damar Hamlin's injury, "
                   "so the Fantasy Bowl couldn't be finished.")

for game_type, title in (("playoff", "Playoffs"), ("consolation", "Placement and consolation games")):
    part = one_side[(one_side["game_type"] == game_type) & (one_side["designation"] != "Fantasy Bowl")]
    if part.empty:
        continue
    section(title)
    for week, wk in part.groupby("week"):
        cards = []
        for g in wk.sort_values("designation").itertuples():
            known = not (pd.isna(g.points_for) or pd.isna(g.points_against))
            a = {"code": g.franchise_code, "score": g.points_for if known else None, "won": None if known else g.result == "W"}
            bteam = {"code": g.opponent_code, "score": g.points_against if known else None}
            cards.append(score_tile(a, bteam, season, footer_left=text(g.designation) or f"Week {week}"))
        html(f'<div class="muted small" style="margin:4px 0 -4px">Week {week}</div>' + tiles(cards, 3))
if season == 2020:
    st.caption("Some 2020 consolation games have only a recorded winner, so no score is shown.")

section("Final standings")
final = standings[standings["season"] == season].sort_values(["final_place", "regular_season_rank"])
left, _ = st.columns([3, 2])
with left:
    html(html_table([("Place", "l"), ("Franchise", "l"), ("Team name", "l")],
                    [[f'<span class="yr">{ordinal(r.final_place)}</span>', team(r.franchise_code, season),
                      esc(text(r.team_name))] for r in final.itertuples()]))
footer()
