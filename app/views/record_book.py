"""
app/views/record_book.py
League records: single-game highs and lows, blowouts, close games, combined scores, seasons.
"""

import streamlit as st

from lib import footer, game_label, query, record, table, team_games

st.title("Record book")

top_n = 20
games = team_games().dropna(subset=["points_for", "points_against"])
kind = st.segmented_control("Games", ["All games", "Regular season", "Postseason"], default="All games",
                            required=True)
if kind == "Regular season":
    games = games[games["game_type"] == "regular"]
elif kind == "Postseason":
    games = games[games["game_type"] != "regular"]

number = st.column_config.NumberColumn(format="%.2f")


def ranked(df, column, ascending, n=top_n):
    """Top rows by a column, with record-book ranks (tied values share a rank)."""
    df = df.sort_values([column, "season", "week"], ascending=[ascending, True, True]).head(n).copy()
    df.insert(0, "Rank", df[column].rank(method="min", ascending=ascending).astype(int))
    return df


def when(g):
    return f"{g.season}, {game_label(g._asdict())}"


def score_list(title, column, ascending):
    st.markdown(f"**{title}**")
    rows = ranked(games, column, ascending)
    table([{"Rank": r.Rank, "Team": r.franchise, "Score": r.points_for, "Opponent": r.opponent,
            "Opp score": r.points_against, "Result": r.result, "When": when(r)} for r in rows.itertuples()],
          column_config={"Score": number, "Opp score": number})


decided = games[games["result"] == "W"].assign(margin_abs=lambda d: d["margin"].abs(),
                                                combined=lambda d: d["points_for"] + d["points_against"])


def game_list(title, column, ascending, label):
    st.markdown(f"**{title}**")
    rows = ranked(decided, column, ascending)
    table([{"Rank": r.Rank, "Winner": r.franchise, "Loser": r.opponent,
            "Score": f"{r.points_for:.2f} - {r.points_against:.2f}", label: getattr(r, column), "When": when(r)}
           for r in rows.itertuples()],
          column_config={label: number})


single, margins, combined, seasons = st.tabs(["Single game", "Blowouts & close games", "Combined", "Seasons"])

with single:
    score_list("Highest scores", "points_for", False)
    score_list("Lowest scores", "points_for", True)

with margins:
    game_list("Biggest blowouts", "margin_abs", False, "Margin")
    game_list("Closest games", "margin_abs", True, "Margin")
    st.caption("Ties and games with no recorded score (some 2020 consolation games) aren't in these lists.")

with combined:
    game_list("Highest combined", "combined", False, "Combined")
    game_list("Lowest combined", "combined", True, "Combined")

with seasons:
    st.caption("Regular season only; seasons still in progress are left out. "
               "2020 had 13 regular-season games; 2021 on have 14.")
    standings = query(
        "select st.* from core.v_season_standings st join core.season s using (season) where s.status = 'complete'")
    standings = standings.assign(Record=[record(r.wins, r.losses, r.ties) for r in standings.itertuples()])

    def season_list(title, column, ascending):
        st.markdown(f"**{title}**")
        df = standings.sort_values([column, "points_for"], ascending=[ascending, ascending]).head(10).copy()
        df.insert(0, "Rank", df[column].rank(method="min", ascending=ascending).astype(int))
        table([{"Rank": r.Rank, "Team": r.franchise, "Season": str(r.season), "Record": r.Record,
                "Points for": r.points_for, "Pts / game": r.points_per_game, "Final place": r.final_place}
               for r in df.itertuples()],
              column_config={"Points for": number, "Pts / game": number,
                             "Final place": st.column_config.NumberColumn(format="%d")})

    season_list("Most points in a season", "points_for", False)
    season_list("Fewest points in a season", "points_for", True)
    season_list("Best records", "win_pct", False)
    season_list("Worst records", "win_pct", True)
footer()
