"""
app/views/lineups.py
Start/sit: points left on the bench compared with the best possible lineup.
"""

import altair as alt
import streamlit as st

from lib import SERIES_BLUE, footer, full_name, query, table

st.title("Lineups")
st.caption("How close each team's starting lineup came to the best possible lineup that week, "
           "using the players on its roster. Lineup % = points scored / best possible points.")

weeks = query("select * from core.v_lineup_week")
weeks["franchise"] = weeks["franchise_code"].map(full_name)
seasons = sorted(weeks["season"].unique(), reverse=True)
left, right = st.columns([1, 2])
choice = left.selectbox("Season", ["All seasons"] + [int(s) for s in seasons])
part = st.segmented_control("Weeks", ["Regular season", "All weeks"], default="Regular season", required=True)

df = weeks if choice == "All seasons" else weeks[weeks["season"] == choice]
if part == "Regular season":
    df = df[df["is_regular_season"]]

# --- per-team summary --------------------------------------------------------------------
summary = df.groupby(["franchise_code", "franchise"]).agg(
    weeks=("week", "count"), scored=("actual_points", "sum"), best=("best_points", "sum"),
    left_on_bench=("points_left_on_bench", "sum"), perfect=("points_left_on_bench", lambda s: (s < 0.01).sum()),
    wrong_calls=("wrong_calls", "sum"),
).reset_index()
summary["lineup_pct"] = 100 * summary["scored"] / summary["best"]
summary["per_week"] = summary["left_on_bench"] / summary["weeks"]
summary = summary.sort_values("lineup_pct", ascending=False)

st.subheader("Lineup accuracy by team")
table(
    [{"Team": r.franchise, "Lineup %": r.lineup_pct, "Left on bench / week": r.per_week,
      "Left on bench (total)": r.left_on_bench, "Perfect weeks": f"{int(r.perfect)} of {int(r.weeks)}",
      "Wrong calls": int(r.wrong_calls)} for r in summary.itertuples()],
    column_config={"Lineup %": st.column_config.NumberColumn(format="%.1f%%"),
                   "Left on bench / week": st.column_config.NumberColumn(format="%.2f"),
                   "Left on bench (total)": st.column_config.NumberColumn(format="%.2f")},
)
st.caption("Wrong calls = players who belonged in the best lineup but sat on the bench. "
           "The best lineup is figured with hindsight, so nobody gets 100% every week.")

bars = alt.Chart(summary).mark_bar(color=SERIES_BLUE, cornerRadiusEnd=4, height={"band": 0.7}).encode(
    x=alt.X("per_week:Q", title="Points left on the bench per week"),
    y=alt.Y("franchise:N", sort="-x", title=None),
    tooltip=[alt.Tooltip("franchise:N", title="Team"), alt.Tooltip("per_week:Q", title="Per week", format=".2f"),
             alt.Tooltip("lineup_pct:Q", title="Lineup %", format=".1f")],
)
st.altair_chart(bars.properties(height=30 * len(summary)), width="stretch")

# --- the costliest weeks and bench performances ---------------------------------------------
st.subheader("Costliest lineup weeks")
worst = df.sort_values("points_left_on_bench", ascending=False).head(15)
opp = query("select season, week, franchise_code, opponent_code, points_against, result from core.v_team_game")
worst = worst.merge(opp, on=["season", "week", "franchise_code"], how="left")
table(
    [{"Team": r.franchise, "Season": str(r.season), "Week": int(r.week), "Scored": r.actual_points,
      "Best possible": r.best_points, "Left on bench": r.points_left_on_bench,
      "Result": "" if not isinstance(r.result, str) else
                f"{r.result} vs {full_name(r.opponent_code)} ({r.points_against:.2f})"
                + (" - would have won" if r.result == "L" and r.best_points > r.points_against else "")}
     for r in worst.itertuples()],
    column_config={c: st.column_config.NumberColumn(format="%.2f") for c in ("Scored", "Best possible", "Left on bench")},
)

st.subheader("Biggest games left on the bench")
bench = query(
    """
    select lp.season, lp.week, lp.franchise_code, p.full_name, lp.position, lp.points
    from core.v_lineup_players lp
    join core.player p on p.player_id = lp.player_id
    where lp.in_best_lineup and not lp.is_starter
    """
)
if choice != "All seasons":
    bench = bench[bench["season"] == choice]
bench = bench.sort_values("points", ascending=False).head(15)
table(
    [{"Player": r.full_name, "Pos": r.position, "Points": r.points, "Team": full_name(r.franchise_code),
      "Season": str(r.season), "Week": int(r.week)} for r in bench.itertuples()],
    column_config={"Points": st.column_config.NumberColumn(format="%.2f")},
)
footer()
