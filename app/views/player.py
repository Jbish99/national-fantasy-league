"""
app/views/player.py
Phase 7a: one player's league history. Every week he was on a league roster (2021 on, from Sleeper):
who had him, where he was in the lineup, what he scored for that team, and his real NFL stat line,
opponent, weather, snap share and injury status. Plus his season totals, usage and expected points (7b),
weather splits (all his NFL games from 2021 on; defenses too since 7b) and every league move.

Open a player straight from a link: player?id=<Sleeper player id> (lib.player_url()).
"""

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

from lib import (SERIES_BLUE, SERIES_ORANGE, WEATHER, box_url, chip, city, conditions, esc, footer, full_name, html,
                 html_table, injury_tag, link, logo, nfl_game_text, position_chip, pts, query, require, section,
                 show_chart, signed, snaps_text, stat_cell, stat_strip, team, text, trade_url, week_extras)

require("core.v_player_game_log")

# --- pick a player -------------------------------------------------------------------------
players = query(
    """
    select pw.player_id,
           coalesce(p.full_name, trim(coalesce(p.first_name, '') || ' ' || coalesce(p.last_name, ''))) as name,
           p.position,
           min(pw.season) as first_season,
           max(pw.season) as last_season,
           coalesce(sum(pw.points) filter (where pw.is_starter), 0) as started_points
    from core.player_week pw
    join core.player p on p.player_id = pw.player_id
    group by pw.player_id, p.full_name, p.first_name, p.last_name, p.position
    """
)
if players.empty:
    st.info("No player weeks loaded yet.")
    st.stop()


def years(first, last):
    return str(first) if first == last else f"{first}-{str(last)[-2:]}"


players["label"] = [f"{r.name} ({text(r.position) or '?'}, {years(r.first_season, r.last_season)})"
                    for r in players.itertuples()]
twice = players["label"].duplicated(keep=False)                    # same name, position and years: add the id
players.loc[twice, "label"] = players.loc[twice, "label"] + " #" + players.loc[twice, "player_id"]
labels = dict(zip(players["player_id"], players["label"]))
ids = players.sort_values(["name", "first_season"])["player_id"].tolist()

wanted = st.query_params.get("id")
default = wanted if wanted in labels else players.sort_values("started_points", ascending=False).iloc[0]["player_id"]
pick_col, _ = st.columns([2, 3])
pid = pick_col.selectbox("Player", ids, index=ids.index(default), format_func=labels.get,
                         help="Every player who was on a league roster in a Sleeper season (2021 on). Type to search.")
st.query_params["id"] = pid
me = players[players["player_id"] == pid].iloc[0]

log = query("select * from core.v_player_game_log where player_id = %s order by season, week", (pid,))
extras = week_extras("player_id = %s", (pid,))

# --- header: name, position, who had him ---------------------------------------------------------
stints = []                                                         # consecutive weeks with one team
for r in log.itertuples():
    if stints and stints[-1]["code"] == r.franchise_code and stints[-1]["last"] in (r.season, r.season - 1):
        stints[-1]["last"] = r.season
    else:
        stints.append({"code": r.franchise_code, "first": r.season, "last": r.season})
had = "".join(f'<span>{team(s["code"], s["last"])}<span class="yrs">{years(s["first"], s["last"])}</span></span>'
              for s in stints)
html(f"""<header class="hdr"><div><div class="phdr"><h1 class="title">{esc(me["name"])}</h1>{position_chip(me["position"])}</div>
<p class="lede">On league rosters {years(me["first_season"], me["last_season"])}. Had by:</p>
<div class="had">{had}</div></div></header>""")

started = log[log["is_starter"]]
best = log.sort_values("league_points", ascending=False).iloc[0]
html(stat_strip([
    (f"{len(log)}", "Weeks on a league roster"),
    (f"{len(started)}", "Weeks started"),
    (f"{started['league_points'].sum():,.2f}", "Points as a starter"),
    (f"{started['league_points'].mean():.2f}" if len(started) else "-", "Per start"),
    (f"{best['league_points']:.2f}", f"Best week ({best['season']} wk {best['week']})"),
]))

# --- season by season ------------------------------------------------------------------------
section("Season by season", "Points as Sleeper scored them for the team that had him")
rows = []
for season, s in log.groupby("season", sort=False):
    s_started = s[s["is_starter"]]
    teams_html = " ".join(team(code, season, size=22) for code in dict.fromkeys(s["franchise_code"]))
    top = s.sort_values("league_points", ascending=False).iloc[0]
    rows.append([f'<span class="yr">{season}</span>', teams_html, str(len(s)), str(len(s_started)),
                 pts(s["league_points"].sum()), f'<span class="big-n">{pts(s_started["league_points"].sum())}</span>',
                 f"{s_started['league_points'].mean():.2f}" if len(s_started) else "",
                 f'{pts(top["league_points"])} <span class="muted small">wk {top["week"]}</span>'])
html(html_table([("Season", "l"), ("Team", "l"), ("Weeks", "r"), ("Started", "r"), ("All points", "r"),
                 ("As a starter", "r"), ("Per start", "r"), ("Best week", "r")], rows[::-1]))

# --- usage and expected points ------------------------------------------------------------------
if me["position"] in ("QB", "RB", "WR", "TE"):
    section("Usage and expected points", "All his NFL regular-season games from 2021 on, our scoring")
    try:
        use = query(
            """
            select e.season, count(*) as games, avg(s.offense_pct) as snap_share,
                   avg(e.pass_attempt) as pass_att, avg(e.targets) as targets, avg(e.carries) as carries,
                   avg(coalesce(u.targets_rz, 0) + coalesce(u.carries_rz, 0)) as rz_looks,
                   avg(e.expected_points) as expected, avg(e.actual_points) as actual
            from core.v_expected_league_points e
            join core.player_xref x on x.gsis_id = e.gsis_id
            left join core.nfl_snap_week s on s.gsis_id = e.gsis_id and s.season = e.season and s.week = e.week
            left join core.nfl_usage_week u on u.gsis_id = e.gsis_id and u.season = e.season and u.week = e.week
            where x.player_id = %s
            group by e.season
            order by e.season desc
            """, (pid,))
    except psycopg.Error:
        use = None
        st.caption("Usage needs one database update: run `python -m etl.run_sql --all`, then reload.")
    if use is not None and use.empty:
        st.caption("No usage data on file for him from 2021 on.")
    elif use is not None:
        volume = ("pass_att", "Pass att / g") if me["position"] == "QB" else ("targets", "Targets / g")
        body = [[f'<span class="yr">{int(r.season)}</span>', str(int(r.games)),
                 f"{r.snap_share * 100:.0f}%" if pd.notna(r.snap_share) else "-",
                 f"{getattr(r, volume[0]):.1f}", f"{r.carries:.1f}", f"{r.rz_looks:.1f}",
                 f"{r.expected:.2f}", f'<span class="big-n">{r.actual:.2f}</span>', signed(r.actual - r.expected)]
                for r in use.itertuples()]
        html(html_table([("Season", "l"), ("Games", "r"), ("Snap share", "r"), (volume[1], "r"), ("Carries / g", "r"),
                         ("Red-zone looks / g", "r"), ("Expected pts / g", "r"), ("Actual pts / g", "r"),
                         ("Over expected / g", "r")], body))
        st.caption("Expected points: what an average player would score with his touches (where on the field and "
                   "how often he got the ball), from nflverse's usage model, scored with our league's rules. Actual "
                   "points here use the same scoring parts (no fumbles, return TDs), so they can differ a little from "
                   "Sleeper's. Above zero = he made more of his chances than usual (big plays, TDs); that tends to "
                   "even out over time. Red-zone looks = targets + carries inside the 20.")

# --- weather splits ----------------------------------------------------------------------------


def conditions_table(wx, whose):
    body = []
    for label, flag, _, meaning in WEATHER:
        games = wx[wx[flag].fillna(False).astype(bool)]
        body.append([f'{esc(label)}<div class="muted small">{esc(meaning)}</div>', str(len(games)),
                     f'<span class="big-n">{games["league_points"].mean():.2f}</span>' if len(games) else "-",
                     signed(games["vs_season_avg"].mean()) if len(games) else "-"])
    body.append(['<b>All games</b>', str(len(wx)), f'<span class="big-n">{wx["league_points"].mean():.2f}</span>',
                 '<span class="muted">0.00</span>'])
    html(html_table([("Conditions", "l"), ("Games", "r"), ("Points per game", "r"),
                     (f"vs {whose} season average", "r")], body))


is_def = me["position"] == "DEF"
section("By conditions", ("All its" if is_def else "All his") + " NFL regular-season games from 2021 on, "
        "on a league roster or not")
try:
    if is_def:
        wx = query("select * from core.v_def_week_conditions where sleeper_def_id = %s", (pid,))
    else:
        wx = query("""select w.* from core.v_player_week_conditions w
                      join core.player_xref x on x.gsis_id = w.gsis_id
                      where x.player_id = %s""", (pid,))
except psycopg.Error:
    wx = None
    st.caption("Weather splits need one database update: run `python -m etl.run_sql --all`, then reload.")
if wx is not None and wx.empty:
    st.caption("No NFL games on file from 2021 on.")
elif wx is not None:
    conditions_table(wx, "its" if is_def else "his")
    if is_def:
        st.caption("Defense points are rebuilt from play-by-play with our league's DEF rules for that season (they "
                   "matched Sleeper's exactly on 28 of 29 real 2025 defense-weeks we checked). 'vs its season "
                   "average' compares each game with its average that season. A cold, windy game counts in both rows.")
    else:
        st.caption("Points are rebuilt from each stat line with our league's rules for that season (they equal "
                   "Sleeper's for about 99% of player-weeks). 'vs his season average' compares each game with his "
                   "average that season, so a role change or the switch from full to half PPR doesn't skew it. "
                   "A cold, windy game counts in both rows. A few games can swing a lot, so check the Games column.")

# --- week by week ----------------------------------------------------------------------------
seasons = sorted(log["season"].unique(), reverse=True)
section("Week by week")
choice = st.segmented_control("Season", [str(s) for s in seasons] + (["All"] if len(seasons) > 1 else []),
                              default=str(seasons[0]), required=True, label_visibility="collapsed")
weeks = log if choice == "All" else log[log["season"] == int(choice)]

if choice != "All":
    chart_data = weeks.assign(
        Lineup=weeks["is_starter"].map({True: "Started", False: "On the bench"}),
        Team=weeks["franchise_code"].map(full_name), Opponent=[nfl_game_text(r) for r in weeks.to_dict("records")])
    bars = alt.Chart(chart_data).mark_bar(width={"band": 0.5}, cornerRadiusEnd=4).encode(
        x=alt.X("week:O", title="Week", axis=alt.Axis(labelAngle=0, grid=False)),
        y=alt.Y("league_points:Q", title="Points"),
        color=alt.Color("Lineup:N", scale=alt.Scale(domain=["Started", "On the bench"],
                                                    range=[SERIES_BLUE, SERIES_ORANGE]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("week:O", title="Week"), alt.Tooltip("league_points:Q", title="Points", format=".2f"),
                 "Lineup:N", "Team:N", alt.Tooltip("Opponent:N", title="NFL game")],
    )
    show_chart(bars.properties(height=220))

rows = []
for r in weeks.sort_values(["season", "week"], ascending=False).to_dict("records"):
    when = f"{r['season']} wk {r['week']}" if choice == "All" else f"Week {r['week']}"
    when_html = link(box_url(r["league_game_id"]), esc(when)) if isinstance(r["league_game_id"], str) else esc(when)
    if isinstance(r["league_result"], str) and isinstance(r["league_opponent_code"], str):
        game = f'{r["league_result"]} vs {city(r["league_opponent_code"])}'
    else:
        game = "No league game"
    nfl = f'{r["nfl_team"]} {nfl_game_text(r)}' if isinstance(r["nfl_team"], str) else ""
    x = extras.get((pid, r["season"], r["week"]), {})
    meta = " · ".join(v for v in (nfl, conditions(r), snaps_text(x.get("offense_pct"))) if v)
    tag = injury_tag(x.get("injury_status"), x.get("injury"))
    rows.append(f'<div class="grow"><div class="gwk">{when_html}<span class="pm">{esc(game)}</span></div>'
                f'<div title="{esc(full_name(r["franchise_code"]))}">{logo(r["franchise_code"], r["season"], 26)}</div>'
                f'<div class="gmain"><span class="pm">{esc(meta)}</span><span class="ps">{tag}{stat_cell(r)}</span></div>'
                f'<div class="bslot">{position_chip(r["lineup_slot"])}</div>'
                f'<div class="bpts{"" if r["is_starter"] else " lo"}">{pts(r["league_points"])}</div></div>')
head = ('<div class="grow head"><div>Week</div><div></div><div>NFL game · stat line</div><div>Slot</div>'
        '<div class="r">Points</div></div>')
html(f'<section class="card glog" aria-label="Week by week">{head}{"".join(rows)}</section>')
st.caption("Week links open that league game's box score. Team = the league team that had him. Points are "
           "Sleeper's, under that season's scoring (2021-22 full PPR; 2023 on half PPR plus first-down and TE "
           "bonuses). Stat lines, weather and snap share come from nflverse; 'No stats recorded' = he didn't play, or "
           "played without an offensive stat. O / D / Q = listed Out, Doubtful or Questionable on that week's NFL "
           "injury report (hover for the injury). PA = points the defense's NFL opponent scored.")

# --- league moves --------------------------------------------------------------------------------
moves = query(
    """
    with picks as (
        select dp.season, dp.franchise_code, dp.round, dp.pick_no, dp.pick_no_is_estimated,
               dp.is_keeper or exists (select 1 from core.keeper k where k.season = dp.season
                                       and k.franchise_code = dp.franchise_code and k.player_id = dp.player_id) as kept
        from core.draft_pick dp
        where dp.player_id = %(p)s
    )
    select season, 0 as week, null::timestamptz as at, case when kept then 'kept' else 'drafted' end as move,
           franchise_code, round, pick_no, pick_no_is_estimated, null::int as bid, null::text as transaction_id
    from picks
    union all
    select k.season, 0, null, 'kept', k.franchise_code, null, null, false, null, null
    from core.keeper k
    where k.player_id = %(p)s
      and not exists (select 1 from picks p where p.season = k.season and p.franchise_code = k.franchise_code)
    union all
    select t.season, t.week, t.created_at, t.type || '_' || tp.action, tp.franchise_code, null, null, false,
           case when t.type = 'waiver' then t.waiver_bid end, t.transaction_id
    from core.transaction t
    join core.transaction_player tp on tp.transaction_id = t.transaction_id
    where tp.player_id = %(p)s and t.status = 'complete'
      and not (t.type = 'trade' and tp.action = 'drop')         -- the other team's 'add' already shows the trade
    order by season, week, at nulls first
    """,
    {"p": pid},
)
WHAT = {"drafted": "Drafted", "kept": "Kept", "trade_add": "Traded to", "waiver_add": "Waiver claim",
        "free_agent_add": "Free-agent pickup", "commissioner_add": "Added by the commissioner"}
section("League moves", "Drafts, keepers, trades and pickups")
if moves.empty:
    st.caption("No draft picks or transactions on file for him.")
else:
    body = []
    for m in moves.itertuples():
        what = WHAT.get(m.move, "Dropped" if m.move.endswith("_drop") else m.move.replace("_", " ").capitalize())
        detail = ""
        if m.move == "drafted" and not pd.isna(m.round):
            pick = "" if m.pick_no_is_estimated or pd.isna(m.pick_no) else f", pick {int(m.pick_no)}"
            detail = f"Round {int(m.round)}{pick}"
        elif m.move == "waiver_add" and not pd.isna(m.bid):
            detail = f"${int(m.bid)} FAAB"
        who = team(m.franchise_code, m.season, size=22) if isinstance(m.franchise_code, str) else \
            '<span class="muted">Team not on file</span>'
        move = chip(what, "plain") if m.move in ("drafted", "kept") else esc(what)
        if m.move == "trade_add":                                   # Phase 9: opens the trade in the Trade analyzer
            move = link(trade_url(m.transaction_id), esc(what))
        if detail:
            move += f' <span class="muted small" style="white-space:nowrap">{esc(detail)}</span>'
        body.append([f'<span class="yr">{m.season}</span>', "Draft" if m.week == 0 else f"Week {m.week}", move, who])
    html(html_table([("Season", "l"), ("When", "l"), ("Move", "l"), ("Team", "l")], body, compact=True))
footer()
