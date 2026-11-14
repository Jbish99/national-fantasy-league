"""
app/views/luck.py
Luck: actual record vs the all-play record (every team vs every other team, every week).
"""

import altair as alt
import streamlit as st

from lib import DIVERGING, footer, full_name, query, record, table

st.title("Luck")
st.caption("All-play record: each regular-season week, your score is compared with every other team's score. "
           "Expected wins = all-play win % x games played. Luck = actual wins - expected wins. "
           "Positive luck means you won more than your scores earned.")

ap = query("select * from core.v_all_play")
ap["franchise"] = ap["franchise_code"].map(full_name)
seasons = sorted(ap["season"].unique(), reverse=True)
choice = st.selectbox("Season", ["All seasons"] + [int(s) for s in seasons])

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

table(
    [{"Team": r.franchise, "Record": record(r.wins, r.losses, r.ties), "All-play record": record(r.ap_w, r.ap_l, r.ap_t),
      "All-play %": r.ap_pct, "Expected wins": r.expected, "Actual wins": r.wins + 0.5 * r.ties, "Luck": r.luck}
     for r in df.itertuples()],
    column_config={"All-play %": st.column_config.NumberColumn(format="%.3f"),
                   "Expected wins": st.column_config.NumberColumn(format="%.2f"),
                   "Actual wins": st.column_config.NumberColumn(format="%.1f"),
                   "Luck": st.column_config.NumberColumn(format="%+.2f")},
)

limit = max(abs(df["luck"]).max(), 0.5)
bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4, height={"band": 0.7}).encode(
    x=alt.X("luck:Q", title="Luck (wins above or below expected)", scale=alt.Scale(domain=[-limit, limit])),
    y=alt.Y("franchise:N", sort="-x", title=None),
    color=alt.Color("luck:Q", scale=alt.Scale(domain=[-limit, 0, limit], range=[DIVERGING[0], "#c3c2b7", DIVERGING[2]],
                                              interpolate="rgb"), legend=None),
    tooltip=[alt.Tooltip("franchise:N", title="Team"), alt.Tooltip("luck:Q", title="Luck", format="+.2f"),
             alt.Tooltip("expected:Q", title="Expected wins", format=".2f")],
)
rule = alt.Chart().mark_rule(color="#52514e", strokeWidth=1).encode(x=alt.datum(0))
st.altair_chart((bars + rule).properties(height=30 * len(df)), width="stretch")
st.caption("Blue = luckier than the scores suggest; red = unluckier. Regular season only, official scores.")
footer()
