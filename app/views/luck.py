"""
app/views/luck.py
Luck: actual record vs the all-play record (every team vs every other team, every week).
"""

import altair as alt
import streamlit as st

from lib import (DIVERGING, MUTED, footer, full_name, html, html_table, page_header, query, record, section,
                 show_chart, team)

page_header("Luck", "All-play record: each regular-season week, your score is compared with every other team's "
                    "score. Expected wins = all-play win % x games played. Luck = actual wins - expected wins. "
                    "Positive luck means you won more than your scores earned.")

ap = query("select * from core.v_all_play")
ap["franchise"] = ap["franchise_code"].map(full_name)
seasons = sorted(ap["season"].unique(), reverse=True)
left, _ = st.columns([1, 3])
choice = left.selectbox("Season", ["All seasons"] + [int(s) for s in seasons])

if choice == "All seasons":
    df = ap.groupby(["franchise_code", "franchise"]).agg(
        wins=("wins", "sum"), losses=("losses", "sum"), ties=("ties", "sum"),
        ap_w=("all_play_wins", "sum"), ap_l=("all_play_losses", "sum"), ap_t=("all_play_ties", "sum"),
        expected=("expected_wins", "sum"), luck=("luck", "sum"), seasons=("season", "count")).reset_index()
else:
    df = ap[ap["season"] == choice].rename(columns={"all_play_wins": "ap_w", "all_play_losses": "ap_l",
                                                    "all_play_ties": "ap_t", "expected_wins": "expected"})
df["ap_pct"] = (df["ap_w"] + 0.5 * df["ap_t"]) / (df["ap_w"] + df["ap_l"] + df["ap_t"])
df = df.sort_values("luck", ascending=False)
art_season = None if choice == "All seasons" else choice


def luck_cell(value):
    cls = "pos" if value > 0.005 else "neg" if value < -0.005 else "muted"
    return f'<span class="big-n {cls}">{value:+.2f}</span>'


section("Luckiest to unluckiest")
html(html_table(
    [("Team", "l"), ("Record", "r"), ("All-play record", "r"), ("All-play %", "r"), ("Expected wins", "r"),
     ("Actual wins", "r"), ("Luck", "r")],
    [[team(r.franchise_code, art_season, name="full"), record(r.wins, r.losses, r.ties), record(r.ap_w, r.ap_l, r.ap_t),
      f"{r.ap_pct:.3f}", f"{r.expected:.2f}", f"{r.wins + 0.5 * r.ties:.1f}", luck_cell(r.luck)]
     for r in df.itertuples()]))

limit = max(abs(df["luck"]).max(), 0.5)
bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4, height={"band": 0.7}).encode(
    x=alt.X("luck:Q", title="Luck (wins above or below expected)", scale=alt.Scale(domain=[-limit, limit])),
    y=alt.Y("franchise:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
    color=alt.Color("luck:Q", scale=alt.Scale(domain=[-limit, 0, limit], range=[DIVERGING[0], MUTED, DIVERGING[2]],
                                              interpolate="rgb"), legend=None),
    tooltip=[alt.Tooltip("franchise:N", title="Team"), alt.Tooltip("luck:Q", title="Luck", format="+.2f"),
             alt.Tooltip("expected:Q", title="Expected wins", format=".2f")],
)
rule = alt.Chart().mark_rule(color=MUTED, strokeWidth=1).encode(x=alt.datum(0))
show_chart((bars + rule).properties(height=30 * len(df)))
st.caption("Blue = luckier than the scores suggest; red = unluckier. Regular season only, official scores.")
footer()
