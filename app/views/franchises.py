"""
app/views/franchises.py
One franchise's history: season by season, scoring trend, results against every opponent.
"""

import altair as alt
import pandas as pd
import streamlit as st

from lib import table, CONTEXT_GRAY, SERIES_BLUE, footer, full_name, game_label, ordinal, query, record, team_games, text

st.title("Franchises")

alltime = query("select * from core.v_all_time_standings order by franchise")
code = st.selectbox("Franchise", alltime["franchise_code"].tolist(), format_func=full_name)
info = alltime[alltime["franchise_code"] == code].iloc[0]
standings = query("select * from core.v_season_standings")
mine = standings[standings["franchise_code"] == code].sort_values("season")

years = f"{info.first_season}" + (f"-{info.last_season}" if info.last_season != info.first_season else "")
owners = ", ".join(dict.fromkeys(o for o in mine["owner"] if isinstance(o, str)))
st.caption(f"{years} · {int(info.seasons)} season{'s' if info.seasons != 1 else ''}"
           + (f" · Owner{'s' if ',' in owners else ''}: {owners}" if owners else ""))

m1, m2, m3, m4 = st.columns(4)
m1.metric("Regular-season record", record(info.wins, info.losses, info.ties), border=True)
m2.metric("Points per game", f"{info.points_per_game:.2f}", border=True)
m3.metric("Playoff trips", f"{int(info.playoff_appearances)} of {int(info.seasons)}", border=True)
m4.metric("Titles", int(info.championships), border=True)

# --- season by season ------------------------------------------------------------------
st.subheader("Season by season")
table(
    [{"Season": str(r.season), "Team name": text(r.team_name), "Owner": text(r.owner),
      "Record": record(r.wins, r.losses, r.ties), "Points for": r.points_for, "Points against": r.points_against,
      "Pts / game": r.points_per_game, "Reg. season rank": ordinal(r.regular_season_rank),
      "Playoffs": ("Champion" if r.champion else record(r.playoff_wins, r.playoff_losses)) if r.made_playoffs else "-",
      "Final place": ordinal(r.final_place)}
     for r in mine.sort_values("season", ascending=False).itertuples()],
    hide_index=True,
    column_config={c: st.column_config.NumberColumn(format="%.2f") for c in ("Points for", "Points against",
                                                                             "Pts / game")},
)

# --- scoring trend vs the league ------------------------------------------------------------
st.subheader("Points per game vs the league")
league = standings.groupby("season").apply(
    lambda s: s["points_for"].sum() / (s["wins"] + s["losses"] + s["ties"]).sum(), include_groups=False)
trend = pd.concat([
    pd.DataFrame({"Season": mine["season"].astype(str), "Points per game": mine["points_per_game"],
                  "Series": full_name(code)}),
    pd.DataFrame({"Season": league.index.astype(str), "Points per game": league.values.round(2),
                  "Series": "League average"}),
]).dropna()
trend = trend[trend["Season"].isin(mine["season"].astype(str))]
series_names = [full_name(code), "League average"]
color = alt.Color("Series:N", scale=alt.Scale(domain=series_names, range=[SERIES_BLUE, CONTEXT_GRAY]),
                  legend=alt.Legend(title=None, orient="top"))
base = alt.Chart(trend).encode(
    x=alt.X("Season:O", title=None, axis=alt.Axis(labelAngle=0)),
    y=alt.Y("Points per game:Q", title="Points per game (regular season)", scale=alt.Scale(zero=False)),
    color=color,
    tooltip=["Series:N", "Season:O", alt.Tooltip("Points per game:Q", format=".2f")],
)
lines = base.mark_line(strokeWidth=2).encode(
    strokeDash=alt.condition(alt.datum.Series == "League average", alt.value([4, 3]), alt.value([1, 0])))
points = base.mark_point(filled=True, size=70)
labels = alt.Chart(trend[trend["Season"] == trend["Season"].max()]).mark_text(align="left", dx=8, fontSize=12).encode(
    x="Season:O", y="Points per game:Q", text="Series:N", color=alt.value("#52514e"))
st.altair_chart((lines + points + labels).properties(height=320), width="stretch")
st.caption("Regular-season points per game each season. Dashed gray: league average that season.")

# --- against each opponent -------------------------------------------------------------------
games = team_games()
games = games[games["franchise_code"] == code]
st.subheader("Against each opponent")
vs = games.groupby("opponent_code").agg(
    W=("result", lambda s: (s == "W").sum()), L=("result", lambda s: (s == "L").sum()),
    T=("result", lambda s: (s == "T").sum()), PF=("points_for", "mean"), PA=("points_against", "mean"),
    Games=("game_id", "count")).reset_index()
vs = vs.assign(pct=(vs["W"] + 0.5 * vs["T"]) / vs["Games"]).sort_values(["pct", "Games"], ascending=[False, False])
table(
    [{"Opponent": full_name(r.opponent_code), "Record": record(r.W, r.L, r.T), "Games": r.Games,
      "Win %": r.pct, "Pts / game": r.PF, "Allowed / game": r.PA} for r in vs.itertuples()],
    hide_index=True,
    column_config={"Win %": st.column_config.NumberColumn(format="%.3f"),
                   "Pts / game": st.column_config.NumberColumn(format="%.2f"),
                   "Allowed / game": st.column_config.NumberColumn(format="%.2f")},
)

with st.expander("Every game"):
    table(
        [{"Season": str(g.season), "Game": game_label(g._asdict()), "Opponent": g.opponent, "Result": g.result,
          "Score": None if pd.isna(g.points_for) else g.points_for,
          "Opp score": None if pd.isna(g.points_against) else g.points_against}
         for g in games.sort_values(["season", "week"], ascending=False).itertuples()],
        hide_index=True,
        column_config={"Score": st.column_config.NumberColumn(format="%.2f"),
                       "Opp score": st.column_config.NumberColumn(format="%.2f")},
    )
footer()
