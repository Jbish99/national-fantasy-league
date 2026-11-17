"""
app/views/rankings.py
Phase 12: Player rankings. Where every NFL player stands in our league's scoring, season by season or all-time
since 2021, overall or within his position, on the stat you pick. Plus each league team's depth chart with every
starter's NFL finish, and how the ranks work.

Ranks come from db/schema/019_player_rankings.sql (core.v_player_season_rank / v_player_career_rank): every NFL
player with a stat line, our league's scoring for that season, weeks 1-17 (the weeks the league plays). The list
on this page re-ranks within whatever is shown (position, league players only), so "RB4" here can differ from
the season finish on the Players page when "League players only" is on.

Open it on a season / position / stat: rankings?season=2025&pos=RB&stat=ppg (lib.rankings_url()); season=all =
all-time.
"""

import pandas as pd
import psycopg
import streamlit as st

from lib import (POSITION_PLURAL, POSITION_WORD, city, esc, footer, full_name, html, html_table, link, logo,
                 ordinal, page_header, player_url, position_chip, query, rank_chip, rank_label, require, section,
                 story_block, team)
from story import ROLE_NAMES, ROLE_RULES, player_line, player_role

require("core.v_player_season_rank")

page_header("Player rankings", "Where every NFL player stands in our league's scoring: by season or all-time since "
                               "2021, against the whole league or his own position.")

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
OFFENSE = ("QB", "RB", "WR", "TE")
FIRST_ROWS = 50


class Stat:
    """One rankable stat. rate = a share or average (needs the minimum games, no per-game column);
    positions = only those positions have it; league = only league starters have it."""

    def __init__(self, key, label, column, fmt="{:,.0f}", digits=0, group="Fantasy", rate=False, positions=None,
                 higher=True, league=False, help_text=""):
        self.key, self.label, self.column, self.fmt, self.digits = key, label, column, fmt, digits
        self.group, self.rate, self.positions, self.higher, self.league = group, rate, positions, higher, league
        self.help = help_text

    def show(self, value):
        if value is None or pd.isna(value):
            return "-"
        return self.fmt.format(float(value))


PCT = "{:.0%}"
STATS = [
    Stat("ppg", "Points per game", "points_per_game", "{:.2f}", 2, rate=True),
    Stat("points", "Total points", "points", "{:,.2f}", 2),
    Stat("por", "Points over replacement", "points_over_replacement", "{:+,.1f}", 1,
         help_text="Points minus what a replacement-level player at his position scored over the same weeks"),
    Stat("started", "Points as a league starter", "league_started_points", "{:,.2f}", 2, league=True, help_text="Sleeper points while in a league team's starting lineup"),
    Stat("consistency", "Consistency (starter-worthy weeks)", "consistency", PCT, 3, rate=True,
         help_text="Share of his games inside the top N at his position, N = how many our league starts"),
    Stat("starter_weeks", "Starter-worthy weeks", "starter_weeks"),
    Stat("top1", "Weeks as the No. 1 at his position", "top1_weeks"),
    Stat("boom", "Boom weeks (20+ points)", "boom_weeks"),
    Stat("bust", "Bust weeks (under 5 points)", "bust_weeks"),
    Stat("best", "Best single week", "best_week", "{:.2f}", 2),
    Stat("expected", "Expected points", "expected_points", "{:,.1f}", 1, positions=OFFENSE),
    Stat("poe", "Points over expected", "points_over_expected", "{:+,.1f}", 1, positions=OFFENSE),
    Stat("pass_yd", "Passing yards", "passing_yards", group="Passing", positions=("QB",)),
    Stat("pass_td", "Passing TDs", "passing_tds", group="Passing", positions=("QB",)),
    Stat("int", "Interceptions thrown", "interceptions", group="Passing", positions=("QB",)),
    Stat("rush_yd", "Rushing yards", "rushing_yards", group="Rushing", positions=OFFENSE),
    Stat("rush_td", "Rushing TDs", "rushing_tds", group="Rushing", positions=OFFENSE),
    Stat("carries", "Carries", "carries", group="Rushing", positions=OFFENSE),
    Stat("carry_share", "Share of his team's carries", "carry_share", PCT, 3, group="Rushing", rate=True,
         positions=("QB", "RB")),
    Stat("targets", "Targets", "targets", group="Receiving", positions=("RB", "WR", "TE")),
    Stat("receptions", "Receptions", "receptions", group="Receiving", positions=("RB", "WR", "TE")),
    Stat("rec_yd", "Receiving yards", "receiving_yards", group="Receiving", positions=("RB", "WR", "TE")),
    Stat("rec_td", "Receiving TDs", "receiving_tds", group="Receiving", positions=("RB", "WR", "TE")),
    Stat("target_share", "Share of his team's targets", "target_share", PCT, 3, group="Receiving", rate=True,
         positions=("RB", "WR", "TE")),
    Stat("rz", "Red-zone looks (targets + carries inside the 20)", "red_zone_looks", group="Usage",
         positions=OFFENSE),
    Stat("snap_share", "Snap share", "snap_share", PCT, 3, group="Usage", rate=True, positions=OFFENSE),
    Stat("fg", "Field goals made", "fg_made", group="Kicking", positions=("K",)),
    Stat("fg50", "Field goals from 50+", "fg_made_50", group="Kicking", positions=("K",)),
    Stat("sacks", "Sacks", "def_sacks", "{:g}", 1, group="Defense", positions=("DEF",)),
    Stat("takeaways", "Takeaways (INT + fumble recoveries)", "takeaways", group="Defense", positions=("DEF",)),
    Stat("def_td", "Defensive and return TDs", "def_tds", group="Defense", positions=("DEF",)),
    Stat("pa", "Fewest points allowed per game", "def_points_allowed_pg", "{:.1f}", 1, group="Defense", rate=True,
         positions=("DEF",), higher=False),
]
BY_KEY = {s.key: s for s in STATS}


# --- data --------------------------------------------------------------------------------------------------
def league_codes(r):
    """The league teams that had him (in order), from the league_teams text ('SDP,GRL'); [] for nobody."""
    value = r.get("league_teams")
    return [c for c in value.split(",") if c] if isinstance(value, str) else []


def _season_list():
    return [int(s) for s in query("select distinct season from core.v_league_season_weeks order by season desc")["season"]]


def load(season):
    """Season rows (season = an int) or all-time rows (season = None) as one DataFrame with a few extra columns."""
    if season is None:
        df = query("select * from core.v_player_career_rank")
        df["on_league_roster"] = df["league_seasons"].fillna(0) > 0
    else:
        df = query("select * from core.v_player_season_rank where season = %s", (season,))
        df["on_league_roster"] = df["league_weeks"].fillna(0) > 0
    if df.empty:
        return df
    df["takeaways"] = df["def_interceptions"].astype(float) + df["def_fumble_recoveries"].astype(float)
    return df


try:
    season_list = _season_list()
except psycopg.Error:
    season_list = []
if not season_list:
    st.info("No NFL stats loaded yet.")
    st.stop()
league_ids = set(query("select distinct player_id from core.player_week")["player_id"])   # who has a Players page

rank_tab, depth_tab, how_tab = st.tabs(["Rankings", "Depth charts", "How rankings work"])

# --- the rankings ------------------------------------------------------------------------------------------
with rank_tab:
    params = st.query_params
    want_season = params.get("season")
    season_options = [str(s) for s in season_list] + ["All-time"]
    season_default = "All-time" if want_season == "all" else want_season if want_season in season_options \
        else season_options[0]
    c1, c2, c3 = st.columns([1.1, 2.6, 1.5])
    season_pick = c1.selectbox("Season", season_options, index=season_options.index(season_default),
                               help="A season = NFL weeks 1-17, the weeks the league plays. All-time = 2021 on.")
    season = None if season_pick == "All-time" else int(season_pick)
    data = load(season)
    have = [p for p in POSITIONS if p in set(data["position"])]
    position_options = ["All"] + have
    want_pos = params.get("pos")
    position = c2.segmented_control("Position", position_options,
                                    default=want_pos if want_pos in position_options else "All", required=True)
    usable = [s for s in STATS if position == "All" or s.positions is None or position in s.positions]
    want_stat = params.get("stat")
    stat_default = want_stat if want_stat in {s.key for s in usable} else "ppg"
    stat = BY_KEY[c3.selectbox("Stat", [s.key for s in usable], index=[s.key for s in usable].index(stat_default),
                               format_func=lambda k: f"{BY_KEY[k].group}: {BY_KEY[k].label}")]
    league_only = st.toggle("League players only", value=False,
                            help="Only players who were on a league roster that season (all-time: in any season "
                                 "since 2021). Off = every NFL player.")
    st.query_params.update({"season": "all" if season is None else str(season), "pos": position, "stat": stat.key})
    if stat.help:
        st.caption(stat.help + ".")

    # the pool: position, group, the stat's positions, minimum games for rates, league starters for 'started'
    pool = data.copy()
    if position != "All":
        pool = pool[pool["position"] == position]
    elif stat.positions:
        pool = pool[pool["position"].isin(stat.positions)]
    if league_only:
        pool = pool[pool["on_league_roster"]]
    if stat.rate:
        pool = pool[pool["qualified"].fillna(False).astype(bool)]
    if stat.league:
        pool = pool[pool["league_starts"].fillna(0) > 0]
    pool = pool[pool[stat.column].notna()].copy()

    when = f"in {season}" if season else "since 2021"
    who = "league players" if league_only else "NFL players"
    if pool.empty:
        st.info("Nobody to rank with these choices.")
    else:
        shown = pool[stat.column].astype(float).round(stat.digits)
        pool["rk"] = shown.rank(method="min", ascending=not stat.higher).astype(int)
        pool["tied"] = shown.duplicated(keep=False)
        pool["prk"] = pool.assign(v=shown).groupby("position")["v"].rank(method="min", ascending=not stat.higher).astype(int)
        pool["ptied"] = pool.assign(v=shown).groupby("position")["v"].transform(lambda v: v.duplicated(keep=False))
        pool["pn"] = pool.groupby("position")["position"].transform("size")
        pool = pool.sort_values(["rk", "points", "player_name"], ascending=[True, False, True])
        n = len(pool)
        rows = pool.to_dict("records")

        def per_value(r):
            """The second number under the stat: per game for counting stats, the total for points per game..."""
            games = float(r["games"])
            if stat.key == "ppg":
                return f"{float(r['points']):,.1f} total"
            if stat.key == "started":
                starts = r.get("league_starts")
                return f"{float(r['league_started_points']) / float(starts):.2f} a start" if starts else ""
            if stat.key == "consistency":
                return f"{int(r['starter_weeks'])} of {int(games)} weeks"
            if stat.key == "bust":
                return f"{float(r['bust_weeks']) / games:.0%} of games"
            if stat.key == "best":
                label = rank_label(r["position"], r.get("best_week_rank"))
                return f"{label} that week" if label else ""
            if stat.key == "carry_share":
                return f"{int(r['carries'])} carries"
            if stat.key == "target_share":
                return f"{int(r['targets'])} targets"
            if stat.rate:
                return ""
            value = r.get(stat.column)
            return "" if value is None or pd.isna(value) or not games else f"{float(value) / games:.2f} a game"

        def meta(r):
            """Position · NFL team · league team logos · role, for the line under the name."""
            bits = [position_chip(r["position"])] if position == "All" else []
            if isinstance(r.get("nfl_team"), str):
                bits.append(f'<span>{esc(r["nfl_team"])}</span>')
            codes = league_codes(r)
            if codes:
                logos = "".join(f'<span title="{esc(full_name(c))}">{logo(c, season, 18)}</span>' for c in codes[:4])
                more = f'<span>+{len(codes) - 4}</span>' if len(codes) > 4 else ""
                bits.append(f'<span class="rlogos">{logos}{more}</span>')
            role = player_role(r)
            if role:
                bits.append(f'<span>{esc(ROLE_NAMES.get(role, ""))}</span>')
            return '<span class="rsep">·</span>'.join(bits)

        def row_html(r):
            pct = (n - r["rk"] + 1) / n
            hover = (f"Ahead of {(n - r['rk']) / n:.0%} of the {n:,} {who} on this list"
                     if r["rk"] > 1 or not r["tied"] else f"Tied for 1st of {n:,}")
            rank_text = f"{'T-' if r['tied'] else ''}{int(r['rk'])}"
            pos_hover = (f"{ordinal(r['prk'])}{' (tied)' if r['ptied'] else ''} of {int(r['pn'])} "
                         f"{POSITION_PLURAL.get(r['position'], r['position'])} on this list")
            chip_html = rank_chip(r["position"], r["prk"], r["ptied"], pos_hover)
            rank_html = (f'<span class="rk-n">{rank_text}</span>{chip_html}' if position == "All" else chip_html) \
                or f'<span class="rk-n">{rank_text}</span>'
            name = esc(r["player_name"])
            if isinstance(r.get("player_id"), str) and r["player_id"] in league_ids:
                name = link(player_url(r["player_id"]), name)
            under = per_value(r)
            return (f'<div class="rrow"><div class="rk">{rank_html}</div>'
                    f'<div class="rp"><span class="rn">{name}</span><span class="rm">{meta(r)}</span>'
                    f'<span class="rk-line">{esc(player_line(r, season))}</span></div>'
                    f'<div class="rv"><b>{esc(stat.show(r[stat.column]))}</b>'
                    f'{f"<span>{esc(under)}</span>" if under else ""}<span>{int(r["games"])} games</span></div>'
                    f'<div class="rb"><div class="pbar" title="{esc(hover)}"><span style="width:{pct * 100:.1f}%">'
                    f'</span></div></div></div>')

        def ranked_list(part):
            head = (f'<div class="rrow head"><div>Rank</div><div>Player</div><div class="r">{esc(stat.label)}</div>'
                    f'<div></div></div>')
            return f'<section class="card rlist" aria-label="Rankings">{head}{"".join(row_html(r) for r in part)}</section>'

        # leaders: the top of each position (All) or the top three (one position), with scouting reports
        leaders = ([next(r for r in rows if r["position"] == p) for p in POSITIONS if any(r["position"] == p for r in rows)]
                   if position == "All" else rows[:3])
        cards = []
        for r in leaders:
            label = rank_label(r["position"], r["prk"], r["ptied"])
            codes = league_codes(r)
            had = (team(codes[-1], season, size=22) if codes else
                   f'<span class="muted small">{esc(r.get("nfl_team") or "")} · not on a league roster</span>')
            name = esc(r["player_name"])
            if isinstance(r.get("player_id"), str) and r["player_id"] in league_ids:
                name = link(player_url(r["player_id"]), name)
            also = "" if stat.key in ("points", "ppg") else f" · {float(r['points']):,.1f} points"
            cards.append(f'<div class="idcard"><div class="id-top">{rank_chip(r["position"], r["prk"], r["ptied"])}'
                         f'{had}</div><div class="id-name">{name}</div>'
                         f'<div class="id-ev">{esc(stat.show(r[stat.column]))} · {esc(stat.label.lower())}{esc(also)}</div>'
                         f'{story_block(player_line(r, season))}</div>')
        section("Top of the depth chart" if position == "All" else f"The best {POSITION_PLURAL[position]} {when}",
                f"{stat.label} {when}, every {'league player' if league_only else 'NFL player'}")
        html(f'<div class="idgrid">{"".join(cards)}</div>')

        section(f"{stat.label} {when}", f"{n:,} {who}" + (f" at {position}" if position != "All" else ""))
        html(ranked_list(rows[:FIRST_ROWS]))
        if n > FIRST_ROWS and st.toggle(f"Show the other {n - FIRST_ROWS:,} players"):
            html(ranked_list(rows[FIRST_ROWS:]))
        notes = ["Points are our league's scoring for that season, rebuilt from every NFL stat line (they equal "
                 "Sleeper's for about 99% of player-weeks), so players who were never on a league roster count too. "
                 "Ranks are within this list: T- = tied. The bar = how far up the list he is."]
        if stat.rate:
            min_games = 17 if season is None else int(data["min_games"].iloc[0])
            notes.append(f"Rates need at least {min_games} games ({'all-time' if season is None else 'half the weeks so far, at most 6'}).")
        if season is None:
            notes.append("All-time = every season since 2021 added up (2020 was on ESPN, with no player data). "
                         "Team = every league team that had him.")
        for note in notes:
            st.caption(note)

# --- depth charts -----------------------------------------------------------------------------------------
with depth_tab:
    d1, _ = st.columns([1, 3])
    depth_season = d1.selectbox("Season", season_list, key="depth_season")
    try:
        starters = query(
            """
            select pw.franchise_code, pw.player_id, p.position,
                   coalesce(p.full_name, trim(coalesce(p.first_name, '') || ' ' || coalesce(p.last_name, ''))) as name,
                   count(*) as starts, sum(pw.points) as points
            from core.player_week pw
            join core.player p on p.player_id = pw.player_id
            join core.season s on s.season = pw.season
            where pw.season = %s and pw.is_starter and pw.week < s.playoff_week_start
            group by pw.franchise_code, pw.player_id, p.position, p.full_name, p.first_name, p.last_name
            """, (depth_season,))
        finishes = query("select player_id, position, pos_rank, pos_tied, pos_count, points, games, starter_line "
                         "from core.v_player_season_rank where season = %s and player_id is not null", (depth_season,))
        groups = query("select * from core.v_position_group_rank where season = %s", (depth_season,))
    except psycopg.Error:
        starters = None
        st.info("Depth charts need one database update: run `python -m etl.run_sql --all`, then reload.")
    if starters is not None and starters.empty:
        st.info("No lineups on file for that season.")
    elif starters is not None:
        finish = {r["player_id"]: r for r in finishes.to_dict("records")}
        group_of = {(r["franchise_code"], r["position"]): r for r in groups.to_dict("records")}
        order = (starters.groupby("franchise_code")["points"].sum().sort_values(ascending=False).index.tolist())
        cards = []
        for code in order:
            mine = starters[starters["franchise_code"] == code]
            rows_html = []
            headliners = []                                   # (finish, points, name) of every non-DEF starter
            for pos in POSITIONS:
                room = mine[mine["position"] == pos].sort_values(["points", "name"], ascending=[False, True])
                if room.empty:
                    continue
                bits = []
                for r in room.head(3).to_dict("records"):
                    f = finish.get(r["player_id"])
                    chip_html = ""
                    if f:
                        tip = (f"{ordinal(f['pos_rank'])}{' (tied)' if f['pos_tied'] else ''} of "
                               f"{int(f['pos_count'])} {POSITION_PLURAL.get(pos, pos)} in {depth_season} "
                               f"({float(f['points']):,.1f} points, every NFL player)")
                        chip_html = rank_chip(f["position"], f["pos_rank"], f["pos_tied"], tip)
                        if pos != "DEF" and not pd.isna(f["starter_line"]):   # best finish vs the starter line
                            headliners.append((int(f["pos_rank"]) / float(f["starter_line"]), -float(r["points"]),
                                               r["name"], r, f))
                    name = link(player_url(r["player_id"]), esc(r["name"]))
                    bits.append(f'{name}{chip_html} <span class="muted">{int(r["starts"])} st</span>')
                if len(room) > 3:
                    bits.append(f'<span class="muted">+{len(room) - 3} more</span>')
                g = group_of.get((code, pos))
                grp = (f'<div class="muted" title="Starters\' points per regular-season week at {pos}: '
                       f'{float(g["points_per_week"]):.2f} (league average {float(g["league_average"]):.2f})">'
                       f'{POSITION_WORD.get(pos, pos).capitalize()} room: {ordinal(g["group_rank"])}'
                       f'{" (tied)" if g["group_tied"] else ""} of {int(g["teams"])}</div>' if g else "")
                rows_html.append(f'<div class="id-row"><span class="id-kind">{esc(pos)}</span>'
                                 f'<div class="depth">{", ".join(bits)}{grp}</div></div>')
            headline = ""
            if headliners:
                *_, r, f = min(headliners, key=lambda h: h[:3])
                headline = (f"Headliner: {r['name']}, the {rank_label(f['position'], f['pos_rank'], f['pos_tied'])} "
                            f"among every NFL {POSITION_WORD.get(f['position'], f['position'])} in {depth_season}.")
            cards.append(f'<div class="idcard"><div class="id-top">{team(code, depth_season, size=26)}</div>'
                         f'{"".join(rows_html)}{story_block(headline, "Depth chart")}</div>')
        section(f"Depth charts, {depth_season}", "Regular-season starters by position, with their NFL finish")
        html(f'<div class="idgrid">{"".join(cards)}</div>')
        st.caption("Every player who started for the team in the regular season, most starter points first (top 3 per "
                   "position), with his NFL finish that season: where his total points ranked among every NFL "
                   "player at his position (weeks 1-17, our scoring). st = starts for that team. Room = the team's "
                   "position group ranked by starters' points per regular-season week (as on the Positions page). "
                   "Headliner = the starter who finished furthest inside the league's starter line at his position "
                   "(RB8 of 34 beats K7 of 12; defenses left out).")

# --- how it works -----------------------------------------------------------------------------------------
with how_tab:
    section("How rankings work")
    st.markdown(
        """
- **Points** are our league's scoring for that season (2021-22 full PPR; 2023 on half PPR plus first-down and TE
  bonuses; kicking from 2026), rebuilt from every NFL stat line, so every NFL player can be ranked, not just the ones
  on a league roster. They equal Sleeper's for about 99% of player-weeks. Defenses use the rebuilt DEF points.
- **Weekly rank** ("WR2 that week", on the Players page and Box scores): where his week ranked among every NFL
  player at his position who recorded a stat that week. Bench weeks count too: it rates the performance, not the
  lineup call. Ties share a rank: **T-WR12**.
- **Position** = Sleeper's (what our lineups use; a fullback counts as a RB). A position is ranked only in seasons
  the league had a lineup spot for it, so kickers start in 2026.
- **Season** = NFL weeks 1 through 17, the weeks the league plays (week 18 doesn't count for anyone).
  **All-time** = every season since 2021 added up (2020 was on ESPN, with no player data).
- **NFL finish** (RB4) = his rank by total points that season. **Per-game ranks** need 6 games in a season (half
  the weeks so far in a season in progress), or 17 games all-time.
- **Points over replacement** = his points minus what a replacement-level player at his position scored over the
  same weeks (the Draft page's and Trade analyzer's replacement levels).
- **Starter-worthy week** = a week inside the top N at his position, N = how many our league starts there (table
  below). **Consistency** = the share of his games that were starter-worthy.
- **Boom week** = 20+ points. **Bust week** = under 5 points. **No. 1 weeks** = weeks he was the top scorer at his
  position (a tie for No. 1 counts).
- **League players only** re-ranks among players who were on a league roster that season (all-time: any season).
"""
    )
    try:
        lines = query("select distinct season, position, starter_line from core.v_player_season_rank "   # the copy:
                      "where starter_line is not null")                                                # fast
        grid = lines.pivot(index="season", columns="position", values="starter_line")
        grid = grid.reindex(columns=[p for p in POSITIONS if p in grid.columns]).sort_index(ascending=False)
        body = [[f'<span class="yr">{int(s)}</span>'] +
                [("-" if pd.isna(v) else f"Top {int(v)}") for v in grid.loc[s]] for s in grid.index]
        section("Starter-worthy line by season", "How many our league starts at each position (FLEX shared by RB/WR/TE)")
        html(html_table([("Season", "l")] + [(p, "r") for p in grid.columns], body, compact=True))
    except psycopg.Error:
        pass
    section("Scouting report roles", "The first role a player's numbers fit, from his real usage that season")
    role_rows = []
    test_text = {"rush_ypg_v": "Rushing yards a game", "att_pg_v": "Pass attempts a game",
                 "carry_share_v": "Share of his team's carries", "target_share_v": "Share of his team's targets",
                 "gl_pg_v": "Carries inside the 5, a game",
                 "deep_share_v": "Share of his targets thrown 20+ yards (with 20+ targets)",
                 "rz_pg_v": "Red-zone looks a game", "fg50_v": "Field goals from 50+",
                 "takeaways_pg_v": "Takeaways a game", "sacks_pg_v": "Sacks a game", "pa_pg_v": "Points allowed a game"}
    for pos, rules in ROLE_RULES.items():
        for code, test in rules:
            if test is None:
                rule = "Anyone else at the position"
            else:
                fact, op, value = test
                shown = f"{value:.0%}" if fact.endswith("share_v") else f"{value:g}"
                rule = f"{test_text.get(fact, fact)}: {'at least' if op == '>=' else 'at most'} {shown}"
            role_rows.append([position_chip(pos), f"<b>{esc(ROLE_NAMES.get(code, code))}</b>", esc(rule)])
    html(html_table([("Position", "l"), ("Role", "l"), ("When", "l")], role_rows, compact=True))
    st.caption("Shares are of his team's carries or targets in the games he played. Edit the roles and their "
               "wording in app/story.py.")
footer()
