"""
app/views/home.py
Landing page: the latest week (its Game of the Week as the big matchup card, then every result),
this season's standings, and every champion.
"""

import pandas as pd
import streamlit as st

from lib import (box_url, chip, city, footer, full_name, html, html_table, hero, league_art, logo, ordinal,
                 page_header, provisional_weeks, pts, query, records_through, roman, score_tile, section,
                 streak_chip, streaks, team, team_games, tiles)

games = team_games()
standings = query("select * from core.v_season_standings")
season_info = query("select season, status, playoff_teams from core.season").set_index("season")
bowls = query(
    """
    select season, team1_code, team2_code, team1_points, team2_points, winner_code
    from core.game where designation = 'Fantasy Bowl' order by season desc
    """
)
team_names = {(r.season, r.franchise_code): r.team_name for r in standings.itertuples()}

latest_season = int(games["season"].max())
latest_week = int(games.loc[games["season"] == latest_season, "week"].max())
provisional = latest_week in provisional_weeks().get(latest_season, [])
week = games[(games["season"] == latest_season) & (games["week"] == latest_week)]
one_side = week.sort_values(["result", "points_for"], ascending=[False, False]).drop_duplicates("game_id")
records = records_through(latest_season, latest_week)
current = standings[standings["season"] == latest_season].sort_values("regular_season_rank")
rank = dict(zip(current["franchise_code"], current["regular_season_rank"]))

page_header("National Fantasy League",
            f"{standings['season'].nunique()} seasons and {games['game_id'].nunique():,} games since 2020. "
            f"Week {latest_week} of {latest_season} is " + ("in, provisional from Sleeper." if provisional else "final."),
            league_art("league_logo"))

# --- the week's big game: Game of the Week, else the closest game ---------------------------------
gotw = one_side[one_side["is_game_of_week"] == True]  # noqa: E712 (pandas column)
featured = gotw.iloc[0] if len(gotw) else one_side.assign(m=one_side["margin"].abs()).sort_values("m").iloc[0]
label = "Game of the week" if len(gotw) else "Closest game"
winner, loser = featured["franchise_code"], featured["opponent_code"]


def rec(code):
    r = rank.get(code)
    return records.get(code, "") + (f", {ordinal(r)}" if r is not None and not pd.isna(r) else "")


html(hero({"code": winner, "score": featured["points_for"], "season": latest_season, "record": rec(winner)},
          {"code": loser, "score": featured["points_against"], "season": latest_season, "record": rec(loser)},
          label=label, sublabel=f"{latest_season}, week {latest_week}", href=box_url(featured["game_id"])))

# --- every result, and the standings -----------------------------------------------------------------
left, right = st.columns([11, 9], gap="large")
with left:
    section(f"Week {latest_week} results", "Provisional, straight from Sleeper" if provisional else None)
    cards = []
    for g in one_side[one_side["game_id"] != featured["game_id"]].itertuples():
        tags = chip("Game of the week") if g.is_game_of_week else ""
        if isinstance(g.international_location, str) and g.international_location:
            tags += chip(g.international_location, "plain")
        cards.append(score_tile({"code": g.franchise_code, "score": g.points_for, "record": records.get(g.franchise_code)},
                                {"code": g.opponent_code, "score": g.points_against, "record": records.get(g.opponent_code)},
                                latest_season, footer_left="Box score ›", tags=tags, href=box_url(g.game_id)))
    html(tiles(cards))
with right:
    section(f"{latest_season} standings", "Record, then points for")
    playoff_teams = season_info.loc[latest_season, "playoff_teams"]
    streak = streaks(latest_season)
    html(html_table(
        [("#", "l"), ("Team", "l"), ("W-L", "r"), ("Points for", "r"), ("Streak", "r")],
        [[f'<span class="muted">{int(r.regular_season_rank)}</span>', team(r.franchise_code, latest_season),
          records.get(r.franchise_code, ""), pts(r.points_for), streak_chip(streak.get(r.franchise_code))]
         for r in current.itertuples()],
        cut_after=None if pd.isna(playoff_teams) else int(playoff_teams), cut_label="Playoff line",
    ))

# --- champions -------------------------------------------------------------------------------------
section("Champions")
cards = []
next_logo = league_art(kind="special_logo", season=latest_season)
if season_info.loc[latest_season, "status"] != "complete" and latest_season not in set(bowls["season"]):
    pic = (f'<img src="{next_logo}" alt="" style="width:64px;height:64px;object-fit:contain">' if next_logo
           else '<span style="height:64px"></span>')
    cards.append(f'<article class="champ next">{pic}<div class="yr">{latest_season}</div>'
                 f'<div class="who">Fantasy Bowl {roman(latest_season - 2019)}</div>'
                 f'<div class="how">Week 17. {int(season_info.loc[latest_season, "playoff_teams"] or 8)} teams make the playoffs.</div></article>')
for b in bowls.itertuples():
    if pd.isna(b.winner_code):                       # 2022: co-champions
        pics = f'<div style="display:flex;gap:6px">{logo(b.team1_code, b.season, 52)}{logo(b.team2_code, b.season, 52)}</div>'
        cards.append(f'<article class="champ">{pics}<div class="yr">{b.season}</div>'
                     f'<div class="who">{city(b.team1_code)} and {city(b.team2_code)}</div>'
                     '<div class="how">Co-champions. The Bills-Bengals game was cancelled after Damar Hamlin\'s '
                     'injury, so the Fantasy Bowl couldn\'t be finished.</div></article>')
        continue
    loser = b.team2_code if b.winner_code == b.team1_code else b.team1_code
    win_pts = b.team1_points if b.winner_code == b.team1_code else b.team2_points
    lose_pts = b.team2_points if b.winner_code == b.team1_code else b.team1_points
    name = team_names.get((b.season, b.winner_code)) or full_name(b.winner_code)
    score = f"{pts(win_pts, b.season)} to {pts(lose_pts, b.season)} over {city(loser)}" if not pd.isna(win_pts) else f"Over {city(loser)}"
    cards.append(f'<article class="champ">{logo(b.winner_code, b.season, 56)}<div class="yr">{b.season}</div>'
                 f'<div class="who">{name}</div><div class="how">{score}</div></article>')
html(f'<div class="champs">{"".join(cards)}</div>')
footer()
