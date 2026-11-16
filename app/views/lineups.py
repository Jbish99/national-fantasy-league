"""
app/views/lineups.py
Start/sit: points left on the bench compared with the best possible lineup, and (Phase 7b) starters who
were listed Out or Doubtful on the NFL injury report.
"""

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

from lib import (SERIES_BLUE, box_url, chip, city, esc, footer, full_name, html, html_table, injury_tag, link,
                 page_header, player_url, position_chip, pts, query, section, show_chart, team, team_games)

page_header("Lineups", "How close each team's starting lineup came to the best possible lineup that week, using "
                       "the players on its roster. Lineup % = points scored / best possible points.")

weeks = query("select * from core.v_lineup_week")
weeks["franchise"] = weeks["franchise_code"].map(full_name)
seasons = sorted(weeks["season"].unique(), reverse=True)
left, right, _ = st.columns([1, 1.4, 1.6])
choice = left.selectbox("Season", ["All seasons"] + [int(s) for s in seasons])
with right:
    part = st.segmented_control("Weeks", ["Regular season", "All weeks"], default="Regular season", required=True)

df = weeks if choice == "All seasons" else weeks[weeks["season"] == choice]
if part == "Regular season":
    df = df[df["is_regular_season"]]
art_season = None if choice == "All seasons" else choice

# --- per-team summary --------------------------------------------------------------------
summary = df.groupby(["franchise_code", "franchise"]).agg(
    weeks=("week", "count"), scored=("actual_points", "sum"), best=("best_points", "sum"),
    left_on_bench=("points_left_on_bench", "sum"), perfect=("points_left_on_bench", lambda s: (s < 0.01).sum()),
    wrong_calls=("wrong_calls", "sum"),
).reset_index()
summary["lineup_pct"] = 100 * summary["scored"] / summary["best"]
summary["per_week"] = summary["left_on_bench"] / summary["weeks"]
summary = summary.sort_values("lineup_pct", ascending=False)

section("Lineup accuracy by team")
html(html_table(
    [("#", "l"), ("Team", "l"), ("Lineup %", "r"), ("Left on bench / week", "r"), ("Left on bench (total)", "r"),
     ("Perfect weeks", "r"), ("Wrong calls", "r")],
    [[f'<span class="muted">{i}</span>', team(r.franchise_code, art_season, name="full"),
      f'<span class="big-n">{r.lineup_pct:.1f}%</span>', f"{r.per_week:.2f}", f"{r.left_on_bench:,.2f}",
      f"{int(r.perfect)} of {int(r.weeks)}", str(int(r.wrong_calls))]
     for i, r in enumerate(summary.itertuples(), 1)]))
st.caption("Wrong calls = players who belonged in the best lineup but sat on the bench. "
           "The best lineup is figured with hindsight, so nobody gets 100% every week.")

# weeks where every team left the same slot empty: that slot isn't counted that week (e.g. 2021 week 17)
short = query("select season, week, slot, season_slots, usable_slots from core.v_lineup_slots_week "
              "where usable_slots < season_slots order by season, week, slot")
if choice != "All seasons":
    short = short[short["season"] == choice]
for r in short.itertuples():
    st.caption(f"{r.season} week {r.week}: every team's lineup was missing a {r.slot} spot in Sleeper's data "
               f"({r.usable_slots} of {r.season_slots} filled), so that week's best lineup uses {r.usable_slots}.")

section("Points left on the bench per week")
bars = alt.Chart(summary).mark_bar(color=SERIES_BLUE, cornerRadiusEnd=4, height={"band": 0.7}).encode(
    x=alt.X("per_week:Q", title="Points left on the bench per week"),
    y=alt.Y("franchise:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
    tooltip=[alt.Tooltip("franchise:N", title="Team"), alt.Tooltip("per_week:Q", title="Per week", format=".2f"),
             alt.Tooltip("lineup_pct:Q", title="Lineup %", format=".1f")],
)
show_chart(bars.properties(height=30 * len(summary)))

# --- the costliest weeks and bench performances ---------------------------------------------
section("Costliest lineup weeks")
worst = df.sort_values("points_left_on_bench", ascending=False).head(15)
opp = query("select season, week, franchise_code, opponent_code, points_against, result from core.v_team_game")
worst = worst.merge(opp, on=["season", "week", "franchise_code"], how="left")
rows = []
for r in worst.itertuples():
    if isinstance(r.result, str):
        flipped = r.result == "L" and r.best_points > r.points_against
        result = (f'{r.result} vs {esc(city(r.opponent_code))} <span class="muted small">({r.points_against:.2f})</span>'
                  + (" " + chip("Would have won") if flipped else ""))
    else:
        result = ""
    rows.append([team(r.franchise_code, r.season), str(r.season), str(int(r.week)), pts(r.actual_points),
                 pts(r.best_points), f'<span class="big-n">{r.points_left_on_bench:.2f}</span>', result])
html(html_table([("Team", "l"), ("Season", "l"), ("Week", "r"), ("Scored", "r"), ("Best possible", "r"),
                 ("Left on bench", "r"), ("Result", "l")], rows))

section("Biggest games left on the bench")
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
html(html_table([("Player", "l"), ("Pos", "l"), ("Points", "r"), ("Team", "l"), ("Season", "l"), ("Week", "r")],
                [[f"<strong>{esc(r.full_name)}</strong>", position_chip(r.position),
                  f'<span class="big-n">{r.points:.2f}</span>', team(r.franchise_code, r.season), str(r.season),
                  str(int(r.week))] for r in bench.itertuples()]))

# --- starters who were on the injury report as Out or Doubtful (Phase 7b) ------------------------
section("Started while listed Out or Doubtful", "From that week's NFL injury report")
try:
    out = query("select * from core.v_started_while_out")
except psycopg.Error:
    out = None
    st.caption("This list needs one database update: run `python -m etl.run_sql --all`, then reload.")
if out is not None:
    if choice != "All seasons":
        out = out[out["season"] == choice]
    if part == "Regular season":
        out = out[out["is_regular_season"]]
    if out.empty:
        st.caption("Nobody started a player listed Out or Doubtful here.")
    else:
        games = team_games()[["season", "week", "franchise_code", "game_id"]]
        out = out.merge(games, on=["season", "week", "franchise_code"], how="left") \
                 .sort_values(["season", "week"], ascending=False)
        counts = out[out["injury_status"] == "Out"].groupby("franchise_code").size().sort_values(ascending=False)
        most = ""
        if len(counts):
            most = " Most starts of a player listed Out: " + ", ".join(
                f"{full_name(code)} {n}" for code, n in counts.head(3).items()) + "."
        st.caption(f"{len(out)} starts, newest first.{most}")
        body = []
        for r in out.itertuples():
            when = f"{r.season}, week {int(r.week)}"
            body.append([
                f'<span class="tc">{position_chip(r.position)}{link(player_url(r.player_id), esc(r.player_name))}</span>',
                f'{injury_tag(r.injury_status, r.injury)}{esc(r.injury_status)}'
                + (f' <span class="muted small">({esc(r.injury)})</span>' if isinstance(r.injury, str) else ""),
                f'<span class="big-n">{r.points:.2f}</span>', team(r.franchise_code, r.season),
                '<span class="muted" style="white-space:nowrap">'
                + (link(box_url(r.game_id), esc(when)) if isinstance(r.game_id, str) else esc(when)) + "</span>"])
        cols = [("Player", "l"), ("Injury report", "l"), ("Points", "r"), ("Team", "l"), ("When", "l")]
        SHOW = 25
        html(html_table(cols, body[:SHOW], compact=True))
        if len(body) > SHOW:
            with st.expander(f"Show the other {len(body) - SHOW} (oldest last)"):
                html(html_table(cols, body[SHOW:], compact=True))
        st.caption("Doubtful players sometimes play (their points show here). Players on injured reserve aren't on "
                   "the weekly report, so they don't show up in this list.")
footer()
