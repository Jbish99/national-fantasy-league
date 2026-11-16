"""
app/views/record_book.py
League records: single-game highs and lows, blowouts, close games, combined scores, seasons,
and (Phase 7a) the best and worst single-game player performances with their real stat lines.
"""

import psycopg
import streamlit as st

from lib import (box_url, esc, footer, game_label, html, html_table, link, nfl_game_text, ordinal, page_header,
                 player_url, position_chip, pts, query, record, section, stat_cell, team, team_games)

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
    label = link(box_url(g.game_id), f"{g.season}, {esc(game_label(g._asdict()))}")
    return f'<span class="muted" style="white-space:nowrap">{label}</span>'


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


single, margins, combined, seasons, players = st.tabs(["Single game", "Blowouts & close games", "Combined",
                                                       "Seasons", "Players"])

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

with players:
    st.caption("Single-game player scores in official league games, 2021 on (Sleeper; 2020 was on ESPN with no "
               "player detail). Points follow that season's scoring: 2021-22 full PPR; 2023 on half PPR plus "
               "first-down and TE bonuses; kickers by distance from 2026.")
    pick, bench_col = st.columns([3, 1.4])
    with pick:
        position = st.segmented_control("Position", ["All", "QB", "RB", "WR", "TE", "K", "DEF"], default="All",
                                        required=True)
    count_bench = bench_col.toggle("Count bench players", value=False,
                                   help="Off: only players who were in the starting lineup.")
    which = {"All games": "all", "Regular season": "regular", "Postseason": "post"}[kind]

    def performances(ascending, starters_only, n):
        return query(
            f"""
            select * from core.v_player_game_log
            where league_game_id is not null
              and (%(pos)s::text is null or position = %(pos)s)
              and (%(bench)s or is_starter)
              and (%(kind)s = 'all' or (%(kind)s = 'regular') = (league_game_type = 'regular'))
            order by league_points {"asc" if ascending else "desc"}, season, week, player_name
            limit %(n)s
            """,
            {"pos": None if position == "All" else position, "bench": not starters_only, "kind": which, "n": n})

    def player_list(title, df, note=None):
        section(title, note)
        if df.empty:
            st.caption("Nothing here yet.")
            return
        ranks = df["league_points"].rank(method="min", ascending=title.startswith("Worst")).astype(int)
        body = []
        for rank, r in zip(ranks, df.to_dict("records")):
            bench = "" if r["is_starter"] else ' <span class="muted small">(bench)</span>'
            game = link(box_url(r["league_game_id"]), f"{r['season']}, week {r['week']}")
            body.append([f'<span class="muted">{rank}</span>',
                         f'<span class="tc">{position_chip(r["position"])}'
                         f'{link(player_url(r["player_id"]), esc(r["player_name"]))}</span>{bench}',
                         f'<span class="big-n">{pts(r["league_points"])}</span>', stat_cell(r),
                         f'<span class="small" style="white-space:nowrap">{esc(nfl_game_text(r))}</span>', team(r["franchise_code"], r["season"], size=22),
                         f'<span class="muted" style="white-space:nowrap">{game}</span>'])
        html(html_table([("#", "l"), ("Player", "l"), ("Points", "r"), ("Stat line", "l"), ("NFL game", "l"),
                         ("Team", "l"), ("When", "l")], body, compact=True))

    try:
        best_games = performances(False, not count_bench, 25)
        worst_starts = performances(True, True, 15)
    except psycopg.Error:
        st.info("Player records need one database update first: run `python -m etl.run_sql --all` "
                "(or start the Refresh workflow on GitHub), then reload.")
    else:
        player_list("Best single-game performances", best_games)
        player_list("Worst starts", worst_starts, "Lowest scores by a starting player")
footer()
