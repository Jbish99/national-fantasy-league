"""
app/views/positions.py
Positional strengths and weaknesses: starters' points per week by position vs the league average.
Phase 7a: weather by position (how QBs, RBs, WRs, TEs and kickers score in a dome, mild weather, cold and wind).
Phase 12: position rooms ranked 1-12 each season, with the starter who led each room.
"""

import altair as alt
import psycopg
import streamlit as st

from lib import (BG, DIVERGING, POSITION_WORD, TEXT, WEATHER, esc, footer, full_name, html, html_table, link, mix,
                 ordinal, page_header, player_url, query, section, short_name, show_chart, signed, team,
                 text_on)

page_header("Positions", "Each team's starters' points per regular-season week at each position (a FLEX starter "
                         "counts at his own position), compared with the league average that season.")

ps = query("select * from core.v_position_strength where position <> '?'")
if ps.empty:
    st.info("No lineup data yet.")
    st.stop()
seasons = sorted(ps["season"].unique(), reverse=True)
left, _ = st.columns([1, 3])
choice = left.selectbox("Season", [int(s) for s in seasons])
df = ps[ps["season"] == choice].copy()
df["team"] = df["franchise_code"].map(short_name)
df["team_full"] = df["franchise_code"].map(full_name)
order = ["QB", "RB", "WR", "TE", "K", "DEF"]
positions = [p for p in order if p in set(df["position"])] + sorted(set(df["position"]) - set(order))
teams = sorted(df["team"].unique())

limit = max(abs(df["vs_league"]).max(), 1)
df["ink"] = [text_on(mix(DIVERGING, (v + limit) / (2 * limit))) for v in df["vs_league"]]
section("Points per week vs the league average")
x = alt.X("position:N", sort=positions, title=None, axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False))
y = alt.Y("team:N", sort=teams, title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=160))
heat = alt.Chart(df).mark_rect(stroke=BG, strokeWidth=2, cornerRadius=3).encode(
    x=x, y=y,
    color=alt.Color("vs_league:Q", scale=alt.Scale(domain=[-limit, 0, limit], range=DIVERGING, interpolate="rgb"),
                    legend=alt.Legend(title="Points per week vs league average", orient="bottom", gradientLength=240,
                                      titleLimit=400)),
    tooltip=[alt.Tooltip("team_full:N", title="Team"), alt.Tooltip("position:N", title="Position"),
             alt.Tooltip("points_per_week:Q", title="Points per week", format=".2f"),
             alt.Tooltip("league_average:Q", title="League average", format=".2f"),
             alt.Tooltip("vs_league:Q", title="Difference", format="+.2f")],
)
labels = alt.Chart(df).mark_text(fontSize=12, fontWeight=600).encode(   # ink: whichever reads better on the cell
    x=x, y=y, text=alt.Text("vs_league:Q", format="+.1f"),
    color=alt.condition(alt.datum.ink == BG, alt.value(BG), alt.value(TEXT)))
show_chart((heat + labels).properties(height=34 * len(teams)))
st.caption("Blue = above the league average at that position, red = below.")

with st.expander("Show as a table"):
    grid = df.pivot(index="team_full", columns="position", values="points_per_week").reindex(columns=positions)
    avg = df.groupby("position")["league_average"].first().reindex(positions)
    grid.loc["League average"] = avg
    st.dataframe(grid.reset_index().rename(columns={"team_full": "Team"}), hide_index=True,
                 column_config={p: st.column_config.NumberColumn(format="%.2f") for p in positions},
                 height=35 * (len(grid) + 1) + 3)

# --- position rooms ranked (Phase 12) ---------------------------------------------------------------


def room_cell(r):
    """'1st' (gold) + who led the room, for one team and position."""
    rank = f'{"T-" if r["group_tied"] else ""}{ordinal(r["group_rank"])}'
    cls = "rkchip top" if int(r["group_rank"]) <= 3 else "rkchip"
    tip = (f"{r['points_per_week']:.2f} starters' points a week at {r['position']} "
           f"(league average {r['league_average']:.2f}); {ordinal(r['group_rank'])}"
           f"{' (tied)' if r['group_tied'] else ''} of {int(r['teams'])}")
    lead = ""
    if isinstance(r.get("lead_player_id"), str):
        who = link(player_url(r["lead_player_id"]), esc(r["lead_player_name"]))
        lead = f'<div class="muted small">{who},&nbsp;{int(r["lead_starts"])}&nbsp;st</div>'
    return f'<span class="{cls}" title="{esc(tip)}">{esc(rank)}</span>{lead}'


section("Position rooms ranked", f"Each team's starters at a position, ranked 1-{df['franchise_code'].nunique()} "
        f"by points per week in {choice}")
try:
    rooms = query("select * from core.v_position_group_rank where season = %s", (choice,))
except psycopg.Error:                                # 019 not applied yet (right after a push)
    rooms = None
    st.info("Position rooms need one database update: run `python -m etl.run_sql --all`, then reload.")
if rooms is not None and not rooms.empty:
    by = {(r["franchise_code"], r["position"]): r for r in rooms.to_dict("records")}
    room_positions = [p for p in positions if p in set(rooms["position"])]
    # teams in order of their average room rank (best overall depth first)
    team_order = rooms.groupby("franchise_code")["group_rank"].mean().sort_values().index.tolist()
    body = [[team(code, choice, size=22)] + [room_cell(by[(code, p)]) if (code, p) in by
                                              else '<span class="muted">-</span>' for p in room_positions]
            for code in team_order]
    html(f'<div class="tight">{html_table([("Team", "l")] + [(p, "l") for p in room_positions], body, compact=True)}</div>')
    tops = []
    for p in room_positions:
        best = rooms[(rooms["position"] == p) & (rooms["group_rank"] == 1)]
        if len(best) == 1:
            b = best.iloc[0]
            led = (f", led by {b['lead_player_name']}" if isinstance(b["lead_player_name"], str) else "")
            tops.append(f"{full_name(b['franchise_code'])} had the best {POSITION_WORD.get(p, p)} room: "
                        f"{b['points_per_week']:.2f} points a week{led}.")
    if tops:
        st.caption(" ".join(tops))
    st.caption("Regular season, starters only, a FLEX starter counts at his own position (same numbers as the heatmap "
               "above). Teams are listed best average room rank first. Under each rank: the starter who scored the most "
               "for that room (st = starts). Hover a rank for the points. Where each player finished among every NFL "
               "player: the Player rankings page's Depth charts.")

# --- weather by position ---------------------------------------------------------------------
section("Weather by position", "Points per game vs the player's own season average, by conditions")
try:
    wx = query("select * from core.v_position_conditions")
except psycopg.Error:
    wx = None
    st.info("Weather by position needs one database update: run `python -m etl.run_sql --all`, then reload.")
if wx is not None and not wx.empty:
    wx_seasons = sorted(wx["season"].dropna().astype(int).unique(), reverse=True)
    span = st.segmented_control("Seasons", ["All seasons"] + [str(s) for s in wx_seasons], default="All seasons",
                                required=True, label_visibility="collapsed", key="weather_seasons")
    w = wx[wx["season"].isna()] if span == "All seasons" else wx[wx["season"] == int(span)]
    w = w.copy()
    conditions_order = [s for _, _, s, _ in WEATHER]
    w_positions = [p for p in order if p in set(w["position"])]
    wlimit = max(float(w["vs_season_avg"].abs().max()), 1.0)
    w["ink"] = [text_on(mix(DIVERGING, (float(v) + wlimit) / (2 * wlimit))) for v in w["vs_season_avg"]]
    wx_x = alt.X("condition:N", sort=conditions_order, title=None,
                 axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False))
    wx_y = alt.Y("position:N", sort=w_positions, title=None, axis=alt.Axis(ticks=False, domain=False))
    cells = alt.Chart(w).mark_rect(stroke=BG, strokeWidth=2, cornerRadius=3).encode(
        x=wx_x, y=wx_y,
        color=alt.Color("vs_season_avg:Q", scale=alt.Scale(domain=[-wlimit, 0, wlimit], range=DIVERGING,
                                                           interpolate="rgb"),
                        legend=alt.Legend(title="Points per game vs the player's season average", orient="bottom",
                                          gradientLength=240, titleLimit=400)),
        tooltip=[alt.Tooltip("position:N", title="Position"), alt.Tooltip("condition:N", title="Conditions"),
                 alt.Tooltip("vs_season_avg:Q", title="vs season average", format="+.2f"),
                 alt.Tooltip("points_per_game:Q", title="Points per game", format=".2f"),
                 alt.Tooltip("games:Q", title="Games", format=",")],
    )
    cell_text = alt.Chart(w).mark_text(fontSize=12, fontWeight=600).encode(
        x=wx_x, y=wx_y, text=alt.Text("vs_season_avg:Q", format="+.1f"),
        color=alt.condition(alt.datum.ink == BG, alt.value(BG), alt.value(TEXT)))
    show_chart((cells + cell_text).properties(height=56 * len(w_positions)))
    st.caption("Players and defenses that were in a league starting lineup at least once that season, all their "
               "NFL regular-season games that season (2021 on), scored with our league's rules. Each game is "
               "compared with that player's average for the season, so star players and the full-to-half PPR "
               "switch don't skew it. Cold = 40°F or colder, Windy = 15+ mph (a cold, windy game counts in both), "
               "Dome includes closed roofs. Kickers start in 2026. Defense points are rebuilt from play-by-play "
               "with our DEF rules.")
    with st.expander("Show as a table"):
        body = []
        for pos in w_positions:
            row = [f"<b>{pos}</b>"]
            for _, _, cond, _ in WEATHER:
                hit = w[(w["position"] == pos) & (w["condition"] == cond)]
                if hit.empty:
                    row.append('<span class="muted">-</span>')
                else:
                    h = hit.iloc[0]
                    row.append(f'{signed(h["vs_season_avg"])}<div class="muted small">{h["points_per_game"]:.2f} '
                               f'per game, {int(h["games"]):,} games</div>')
            body.append(row)
        html(html_table([("Position", "l")] + [(c, "r") for _, _, c, _ in WEATHER], body, compact=True))
footer()
