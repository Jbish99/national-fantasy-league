"""
app/views/record_book.py
League records: single-game highs and lows, blowouts, close games, combined scores, seasons.
"""

import streamlit as st

from lib import (footer, game_label, html, html_table, ordinal, page_header, pts, query, record, section, team,
                 team_games)

page_header("Record book")

top_n = 20
games = team_games().dropna(subset=["points_for", "points_against"])
kind = st.segmented_control("Games", ["All games", "Regular season", "Postseason"], default="All games",
                            required=True)
if kind == "Regular season":
    games = games[games["game_type"] == "regular"]
elif kind == "Postseason":
    games = games[games["game_type"] != "regular"]


def ranked(df, column, ascending, n=top_n):
    """Top rows by a column, with record-book ranks (tied values share a rank)."""
    df = df.sort_values([column, "season", "week"], ascending=[ascending, True, True]).head(n).copy()
    df.insert(0, "Rank", df[column].rank(method="min", ascending=ascending).astype(int))
    return df


def when(g):
    return f'<span class="muted" style="white-space:nowrap">{g.season}, {game_label(g._asdict())}</span>'


def score_list(title, column, ascending, container):
    with container:
        section(title)
        rows = ranked(games, column, ascending)
        html(html_table(
            [("#", "l"), ("Team", "l"), ("Score", "r"), ("Opponent", "l"), ("Opp score", "r"), ("When", "l")],
            [[f'<span class="muted">{r.Rank}</span>', team(r.franchise_code, r.season),
              f'<span class="big-n">{pts(r.points_for, r.season)}</span>',
              team(r.opponent_code, r.season, size=22), pts(r.points_against, r.season), when(r)]
             for r in rows.itertuples()], compact=True))


decided = games[games["result"] == "W"].assign(margin_abs=lambda d: d["margin"].abs(),
                                                combined=lambda d: d["points_for"] + d["points_against"])


def game_list(title, column, ascending, label, container):
    with container:
        section(title)
        rows = ranked(decided, column, ascending)
        html(html_table(
            [("#", "l"), ("Winner", "l"), ("Loser", "l"), ("Score", "r"), (label, "r"), ("When", "l")],
            [[f'<span class="muted">{r.Rank}</span>', team(r.franchise_code, r.season), team(r.opponent_code, r.season),
              f"{pts(r.points_for, r.season)} - {pts(r.points_against, r.season)}",
              f'<span class="big-n">{getattr(r, column):.2f}</span>', when(r)]
             for r in rows.itertuples()], compact=True))


single, margins, combined, seasons = st.tabs(["Single game", "Blowouts & close games", "Combined", "Seasons"])

with single:
    score_list("Highest scores", "points_for", False, st.container())
    score_list("Lowest scores", "points_for", True, st.container())

with margins:
    game_list("Biggest blowouts", "margin_abs", False, "Margin", st.container())
    game_list("Closest games", "margin_abs", True, "Margin", st.container())
    st.caption("Ties and games with no recorded score (some 2020 consolation games) aren't in these lists.")

with combined:
    game_list("Highest combined", "combined", False, "Combined", st.container())
    game_list("Lowest combined", "combined", True, "Combined", st.container())

with seasons:
    st.caption("Regular season only; seasons still in progress are left out. "
               "2020 had 13 regular-season games; 2021 on have 14.")
    standings = query(
        "select st.* from core.v_season_standings st join core.season s using (season) where s.status = 'complete'")

    def season_list(title, column, ascending, container):
        with container:
            section(title)
            df = standings.sort_values([column, "points_for"], ascending=[ascending, ascending]).head(10).copy()
            df.insert(0, "Rank", df[column].rank(method="min", ascending=ascending).astype(int))
            html(html_table(
                [("#", "l"), ("Team", "l"), ("Season", "l"), ("Record", "r"), ("Points for", "r"), ("Pts / game", "r"),
                 ("Finish", "r")],
                [[f'<span class="muted">{r.Rank}</span>', team(r.franchise_code, r.season), str(r.season),
                  record(r.wins, r.losses, r.ties), pts(r.points_for), f"{r.points_per_game:.2f}",
                  ordinal(r.final_place)] for r in df.itertuples()], compact=True))

    for title, column, ascending in (("Most points in a season", "points_for", False),
                                     ("Fewest points in a season", "points_for", True),
                                     ("Best records", "win_pct", False), ("Worst records", "win_pct", True)):
        season_list(title, column, ascending, st.container())
footer()
