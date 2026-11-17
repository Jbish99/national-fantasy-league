"""
app/views/trades.py
Trade Analyzer (Phase 9): every trade valued over time, who won it, and each team's trade report card.

How a trade is judged (db/schema/015_trade_analyzer.sql, decisions by Jaden 2026-11-16): each team is credited with
what it RECEIVED, as points over a replacement-level starter at the same position in the weeks it started those
players (regular season and playoff games in one total), for as long as the player stayed on that roster, kept
seasons included. A traded pick counts as the player drafted with it. FAAB is listed, not counted.
Net = value received - value given. Won = net of 1 point or more; lost = -1 or less; else even.

Links: /trades?trade=<transaction_id> opens one trade (lib.trade_url).
"""

from collections import defaultdict
from types import SimpleNamespace

import altair as alt
import pandas as pd
import streamlit as st

from lib import (BG, DIVERGING, MUTED, NEG_RED, SERIES_BLUE, SERIES_ORANGE, TEXT, chip, city, esc, footer, full_name, hero,
                 html, html_table, link, mix, ordinal, page_header, player_url, position_chip, query, record,
                 require, section, seasons, short_name, show_chart, signed, stat_strip, team, text_on, trade_url)

SERIES_GREEN = "#199e70"        # 3rd slot of the theme palette (only 3-team trades need it); validated with blue/orange
LINE_COLORS = [SERIES_BLUE, SERIES_ORANGE, SERIES_GREEN]
EVEN = 1.0                      # under 1 point of net value either way = even (same rule as core.v_trade_side)

page_header("Trade analyzer",
            "Each team is credited with what it got: points over a replacement-level starter at the same position, "
            "in the weeks it started those players (regular season and playoffs), for as long as he stayed on that "
            "roster, kept seasons included. A traded pick counts as the player drafted with it. FAAB is listed but "
            "not counted. Net = what you got minus what you gave.")
require("core.v_trade_side")

sides = query("select * from core.v_trade_side")
assets = query(
    """
    select a.*, p.position,
           coalesce(p.full_name, nullif(trim(coalesce(p.first_name, '') || ' ' || coalesce(p.last_name, '')), ''),
                    a.player_id) as player_name
    from core.v_trade_asset_value a
    left join core.player p on p.player_id = a.player_id
    """
)
season_status = dict(zip(seasons()["season"], seasons()["status"]))

if sides.empty:
    st.info("No completed trades on file yet.")
    footer()
    st.stop()


def records(df):
    """Rows as simple objects (r.net, r.value...). Much faster to loop over than DataFrame.itertuples (Phase 11:
    building every trade card that way took ~1.7 s per visit on the real league)."""
    return [SimpleNamespace(**r) for r in df.to_dict("records")]


def best_first(items):
    """Assets in the order the cards show them: most value first (no value last), then player, pick, FAAB."""
    items = sorted(items, key=lambda r: r.asset_type, reverse=True)
    return sorted(items, key=lambda r: (pd.isna(r.value), 0 if pd.isna(r.value) else -r.value))


SIDE_OF = defaultdict(dict)              # transaction_id -> {franchise_code: its side}
for _r in records(sides):
    SIDE_OF[_r.transaction_id][_r.franchise_code] = _r
GOT = defaultdict(list)                  # (transaction_id, receiving team) -> assets it got, best first
for _r in records(assets):
    GOT[(_r.transaction_id, _r.receiver_code)].append(_r)
GOT = defaultdict(list, {k: best_first(v) for k, v in GOT.items()})


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def tight(table_html):
    """A table that tightens up on a phone (smaller padding and logos; CSS .tight in lib.py)."""
    return f'<div class="tight">{table_html}</div>'


def trade_table(s):
    """One row per trade from the per-team sides: teams (best net first), winner, margin, still counting."""
    s = s.sort_values(["transaction_id", "net", "franchise_code"], ascending=[True, False, True])
    g = s.groupby("transaction_id", sort=True)
    top = g.head(1).set_index("transaction_id")
    out = pd.DataFrame({
        "transaction_id": top.index, "season": top["season"].astype(int).to_numpy(),
        "week": top["week"].astype(int).to_numpy(), "created_at": top["created_at"].to_numpy(),
        "teams": g["franchise_code"].agg(list).reindex(top.index).to_numpy(),
        "winner": [c if n >= EVEN else None for c, n in zip(top["franchise_code"], top["net"])],
        "margin": top["net"].astype(float).to_numpy(),
        "total": g["value_received"].sum().reindex(top.index).astype(float).to_numpy(),
        "still": g["still_counting"].any().reindex(top.index).astype(bool).to_numpy()})
    return out.reset_index(drop=True)


def winner(t):
    """The trade's winner code, or None (an even trade). Pandas turns None into NaN, which is truthy."""
    return t["winner"] if isinstance(t["winner"], str) else None


def date_text(ts):
    return f"{ts:%b} {ts.day}, {ts.year}" if isinstance(ts, pd.Timestamp) else ""


def years(first, last):
    first, last = int(first), int(last)
    return str(first) if first == last else f"{first}-{str(last)[-2:]}"


def num(value, digits=1):
    """'+12.4' / '-3.1' / '0.0' ('' when missing)."""
    if value is None or pd.isna(value):
        return ""
    return f"{0:.{digits}f}" if abs(value) < 0.5 * 10 ** -digits else f"{value:+,.{digits}f}"


def usage(r):
    """'Started 9 of 11 weeks · 2022-24, kept 2 seasons' for a valued asset."""
    if not r.weeks_rostered:
        return "Never on their roster in a counted week" if r.asset_type == "player" else "Never played for them"
    first = r.pick_season if r.asset_type == "pick" else r.season
    span = years(first, r.last_season)
    kept = f", kept {int(r.kept_seasons)} season{'s' if r.kept_seasons != 1 else ''}" if r.kept_seasons else ""
    now = " · still on the roster" if r.still_counting else ""
    weeks = int(r.weeks_rostered)
    return f"Started {int(r.weeks_started)} of {weeks} week{'s' if weeks != 1 else ''} · {span}{kept}{now}"


def player_label(r):
    return f'{position_chip(r.position)} {link(player_url(r.player_id), "<strong>" + esc(r.player_name) + "</strong>")}'


PICK_NOTES = {"traded_on": "Traded on before the draft (counted in that trade)",
              "not_drafted_yet": "That draft hasn't happened yet",
              "no_match": "Couldn't match this pick to a drafted player",
              "made_by_other": "Couldn't match this pick to a drafted player"}


def asset_item(r):
    """One <li> for an asset a team received."""
    if r.asset_type == "faab":
        name, meta, value = f"<strong>${int(r.faab_amount)} FAAB</strong>", "Listed only, not counted", ""
    elif r.asset_type == "pick":
        pick = f"{int(r.pick_season)} round {int(r.pick_round)} pick"
        if r.pick_original_code != r.sender_code:
            pick += f" (originally {esc(city(r.pick_original_code))}'s)"
        if r.pick_status == "drafted":
            name = f'<span class="muted">{pick} &rarr;</span> {player_label(r)}'
            meta, value = f"Pick {int(r.pick_no)} · {usage(r)}", num(r.value)
        elif r.pick_status == "keeper":
            name = f"<strong>{pick}</strong>"
            meta, value = f"Used to place a keeper ({esc(r.player_name)}), no new player", num(0)
        else:
            name, meta = f"<strong>{pick}</strong>", PICK_NOTES.get(r.pick_status, "")
            value = "" if r.pick_status == "not_drafted_yet" else num(0)
    else:
        name, meta, value = player_label(r), usage(r), num(r.value)
    return (f'<li><span class="a-main"><span class="a-name">{name}</span><span class="a-meta">{esc(meta)}</span>'
            f'</span><span class="a-val">{value}</span></li>')


def side_html(row, got, won):
    """One team's column of a trade. got: the assets it received, best first (GOT[...])."""
    items = "".join(asset_item(r) for r in got) or '<li><span class="muted">Nothing</span><span></span></li>'
    return (f'<div class="trade-side{" won" if won else ""}"><div class="ts-top">'
            f'{team(row.franchise_code, int(row.season))}'
            f'<div class="ts-val"><b>{num(row.value_received)}</b><span>value received</span></div></div>'
            f'<ul class="assets">{items}</ul></div>')


def verdict(t):
    if winner(t):
        text = f'{city(winner(t))} {"leads" if t["still"] else "won"} by {t["margin"]:.1f}'
        out = chip(text, "gold")
    else:
        out = chip("Even so far" if t["still"] else "Even", "plain")
    if t["still"]:
        out += " " + chip("Still counting", "plain")
    return out


def trade_card(t):
    tid = t["transaction_id"]
    cols = "".join(side_html(SIDE_OF[tid][code], GOT[(tid, code)], code == winner(t)) for code in t["teams"])
    return (f'<article class="trade"><div class="trade-head"><span class="th-l"><strong style="color:{TEXT}">'
            f'{t["season"]} · Week {t["week"]}</strong><span>{esc(date_text(t["created_at"]))}</span></span>'
            f'<span class="th-r">{verdict(t)}{link(trade_url(tid), "Details")}</span></div>'
            f'<div class="trade-sides">{cols}</div></article>')


def finish(r):
    """How the team's season ended, short enough for a phone column."""
    place = r.final_place
    if pd.notna(r.champion) and bool(r.champion):
        return "Champion"
    if pd.notna(place) and int(place) == 2:
        return "Lost the Fantasy Bowl"
    if pd.isna(place) and season_status.get(r.season) != "complete":
        return "In progress"
    where = f" ({ordinal(place)})" if pd.notna(place) else ""
    return ("Playoffs" if pd.notna(r.made_playoffs) and bool(r.made_playoffs) else "Missed playoffs") + where


def before_after(r):
    """'1-4 -> 7-2' on top, '116.7 -> 137.5 ppg' below: the team's regular season before / from the trade week."""
    def rec(games, w, l, t):
        return record(w, l, t) if games else "-"

    def ppg(games, value):
        return f"{value:.1f}" if games else "-"
    return (f'<strong>{rec(r.games_before, r.wins_before, r.losses_before, r.ties_before)} &rarr; '
            f'{rec(r.games_after, r.wins_after, r.losses_after, r.ties_after)}</strong><br>'
            f'<span class="muted small">{ppg(r.games_before, r.ppg_before)} &rarr; '
            f'{ppg(r.games_after, r.ppg_after)} ppg</span>')


trades = trade_table(sides)

# ---------------------------------------------------------------------------------------------
# One trade: /trades?trade=<id>
# ---------------------------------------------------------------------------------------------
wanted = st.query_params.get("trade")
if wanted and wanted in set(trades["transaction_id"]):
    t = trades[trades["transaction_id"] == wanted].iloc[0].to_dict()
    s = sides[sides["transaction_id"] == wanted].set_index("franchise_code").loc[t["teams"]].reset_index()
    a = assets[assets["transaction_id"] == wanted]
    html(link("trades", "&larr; All trades"))

    status = "So far" if t["still"] else "Final"
    label = f"{t['season']} · Week {t['week']} trade"
    extra = chip("Still counting", "plain") if t["still"] else ""
    if len(s) == 2:
        one, two = s.iloc[0], s.iloc[1]
        html(hero({"code": one.franchise_code, "score": one.value_received, "season": t["season"],
                   "record": f"Net {one.net:+.1f}"},
                  {"code": two.franchise_code, "score": two.value_received, "season": t["season"],
                   "record": f"Net {two.net:+.1f}"},
                  label=label, sublabel=date_text(t["created_at"]), winner="l" if winner(t) else None,
                  status=status, margin_text=None if winner(t) else "Even", extra=extra))
    else:
        html(f'<div class="hero-meta" style="display:flex;gap:12px;align-items:center">{chip(label)}'
             f'<span class="muted small">{esc(date_text(t["created_at"]))}</span>{extra}</div>')
        html(stat_strip([(f"{r.value_received:+.1f}", f"{short_name(r.franchise_code)} received (net {r.net:+.1f})")
                         for r in s.itertuples()]))
    st.caption("Big numbers = value each team got: points over a replacement-level starter at the same position, in "
               "the weeks it started what it received (regular season + playoff games), until each player left "
               "that roster. " + ("Players are still on these rosters or picks are still to be drafted, so the "
                                  "numbers can still move." if t["still"] else ""))

    section("What each team got")
    html(f'<div class="trade-sides">{"".join(side_html(r, GOT[(wanted, r.franchise_code)], r.franchise_code == winner(t)) for r in s.itertuples())}</div>')

    weeks = query(
        """
        select receiver_code, asset_id, player_id, season, week, game_kind, is_starter, lineup_slot, points,
               replacement_per_week, points_over_replacement
        from core.v_trade_asset_week
        where transaction_id = %s
        order by season, week
        """,
        (wanted,),
    )

    # --- value over time: each team's running total, week by week ----------------------------
    section("Value over time", "Running total of points over replacement in counted starts")
    counted = weeks[weeks["game_kind"].notna() & weeks["is_starter"]]
    timeline = sorted({(int(r.season), int(r.week)) for r in weeks.itertuples()} | {(t["season"], t["week"])})
    timeline = [sw for sw in timeline if sw >= (t["season"], t["week"])]
    labels = [f"{s_} W{w_}" for s_, w_ in timeline]
    per_week = counted.groupby(["receiver_code", "season", "week"])["points_over_replacement"].sum()
    series = []
    for code in t["teams"]:
        total = 0.0
        for (s_, w_), lab in zip(timeline, labels):
            total += float(per_week.get((code, s_, w_), 0.0))
            series.append({"Team": city(code), "team_full": full_name(code), "When": lab, "Season": s_, "Week": w_,
                           "Value": round(total, 1), "This week": round(float(per_week.get((code, s_, w_), 0.0)), 1)})
    sdf = pd.DataFrame(series)
    names = [city(c) for c in t["teams"]]
    color = alt.Color("Team:N", sort=names, scale=alt.Scale(domain=names, range=LINE_COLORS[:len(names)]),
                      legend=alt.Legend(orient="top", title=None, symbolType="stroke", symbolStrokeWidth=3,
                                        symbolOpacity=1))
    x = alt.X("When:O", sort=labels, title=None,
              axis=alt.Axis(labelAngle=0, labelOverlap="greedy", labelSeparation=14, ticks=False))
    y = alt.Y("Value:Q", title="Value so far")
    lines = alt.Chart(sdf).mark_line(interpolate="step-after", strokeWidth=2).encode(x=x, y=y, color=color)
    hover = alt.Chart(sdf).mark_circle(size=90, opacity=0.001).encode(     # invisible hover targets
        x=x, y=y, detail="Team:N",
        tooltip=[alt.Tooltip("team_full:N", title="Team"), alt.Tooltip("Season:Q", format="d"),
                 alt.Tooltip("Week:Q"), alt.Tooltip("This week:Q", format="+.1f"),
                 alt.Tooltip("Value:Q", title="Value so far", format="+.1f")])
    ends = sdf[sdf["When"] == labels[-1]]
    end_labels = alt.Chart(ends).mark_text(align="right", dy=-9, fontSize=12, fontWeight=600, color=TEXT).encode(
        x=x, y=y, text="Team:N")
    zero = alt.Chart().mark_rule(color=MUTED, strokeWidth=1, strokeDash=[3, 3]).encode(y=alt.datum(0))
    if counted.empty:
        st.caption("Nothing in this trade has been started in a counted week yet.")
    else:
        show_chart((zero + lines + hover + end_labels).properties(height=300))
    st.caption("Steps up when a player the team got starts and beats a replacement-level starter at his position; "
               "steps down when he starts and falls short. Bench weeks, consolation games and weeks after "
               "elimination don't count. Hover for the week.")

    # --- before / after and how the season ended ----------------------------------------------
    section("Before and after", f"{t['season']} regular season: record and points per game before the trade "
                                f"week and from it on")
    html(tight(html_table(
        [("Team", "l"), ("Net", "r"), ("Before → after", "r"), ("Season", "l")],
        [[team(r.franchise_code, t["season"]), f'<span class="big-n">{signed(r.net, 1)}</span>', before_after(r),
          esc(finish(r))] for r in s.itertuples()])))
    faab_moved = s[(s["faab_received"] > 0) | (s["faab_sent"] > 0)]
    if not faab_moved.empty:
        st.caption("FAAB: " + "; ".join(f"{full_name(r.franchise_code)} got ${int(r.faab_received)}, sent "
                                        f"${int(r.faab_sent)}" for r in faab_moved.itertuples())
                   + ". FAAB isn't counted in the value.")

    # --- season by season -------------------------------------------------------------------
    valued = weeks.merge(a[["asset_id", "player_name", "position"]], on="asset_id")
    if not valued.empty:
        with st.expander("Season by season, player by player"):
            valued["counted_start"] = valued["game_kind"].notna() & valued["is_starter"]
            valued["started_points"] = valued["points"].where(valued["counted_start"], 0)
            valued["value"] = valued["points_over_replacement"].where(valued["counted_start"], 0).fillna(0)
            g = valued.groupby(["receiver_code", "player_name", "position", "season"]).agg(
                weeks=("week", "count"), starts=("counted_start", "sum"), points=("started_points", "sum"),
                value=("value", "sum"), repl=("replacement_per_week", "first")).reset_index()
            g = g.sort_values(["receiver_code", "player_name", "season"])
            html(tight(html_table(
                [("Team", "l"), ("Player", "l"), ("Season", "l"), ("Weeks on roster", "r"), ("Counted starts", "r"),
                 ("Points in those starts", "r"), ("Replacement / week", "r"), ("Value", "r")],
                [[team(r.receiver_code, int(r.season)), f"{position_chip(r.position)} {esc(r.player_name)}",
                  str(int(r.season)), str(int(r.weeks)), str(int(r.starts)), f"{r.points:,.1f}",
                  "" if pd.isna(r.repl) else f"{r.repl:.1f}", num(r.value)] for r in g.itertuples()],
                compact=True)))
            st.caption("Replacement / week = what the Nth-best player at his position scored per week that season "
                       "(N = starters league-wide, the Draft page's levels). Value = points in counted starts minus "
                       "that, once per start.")
    footer()
    st.stop()


# ---------------------------------------------------------------------------------------------
# All trades
# ---------------------------------------------------------------------------------------------
codes = sorted(set(sides["franchise_code"]), key=full_name)
c1, c2, _ = st.columns([1, 1.4, 1.6])
season_choice = c1.selectbox("Season", ["All seasons"] + sorted(trades["season"].unique().tolist(), reverse=True),
                             help="The season the trade was made. Its value still includes every later season.")
team_choice = c2.selectbox("Team", ["All teams"] + codes, format_func=lambda c: c if c == "All teams" else full_name(c),
                           help="Picks one team's trades on the Trades and Timeline tabs. Report cards and Trade "
                                "partners always show every team.")

tf = trades if season_choice == "All seasons" else trades[trades["season"] == season_choice]
sf = sides[sides["transaction_id"].isin(tf["transaction_id"])]
af = assets[assets["transaction_id"].isin(tf["transaction_id"])]
art_season = None if season_choice == "All seasons" else season_choice

list_tab, cards_tab, partners_tab, time_tab = st.tabs(["Trades", "Report cards", "Trade partners", "Timeline"])

# --- trades ----------------------------------------------------------------------------------
with list_tab:
    shown = tf if team_choice == "All teams" else tf[tf["teams"].apply(lambda teams: team_choice in teams)]
    if shown.empty:
        st.info("No completed trades in this selection.")
    else:
        sa = af[af["transaction_id"].isin(shown["transaction_id"])]
        top = shown.loc[shown["margin"].idxmax()]
        html(stat_strip([
            (f"{len(shown)}", "trades"),
            (f"{int((sa['asset_type'] == 'player').sum())}", "players moved"),
            (f"{int((sa['asset_type'] == 'pick').sum())}", "draft picks moved"),
            (f"${int(sa['faab_amount'].fillna(0).sum())}", "FAAB moved"),
            (f"{top['margin']:.1f}", f"most lopsided ({city(winner(top))}, {top['season']})" if winner(top)
             else "most lopsided"),
        ]))
        order = st.segmented_control("Sort", ["Newest", "Most lopsided", "Closest", "Most value moved"],
                                     default="Newest", required=True)
        shown = {"Newest": shown.sort_values("created_at", ascending=False),
                 "Most lopsided": shown.sort_values("margin", ascending=False),
                 "Closest": shown.sort_values("margin", ascending=True),
                 "Most value moved": shown.sort_values("total", ascending=False)}[order]
        first = 15
        html('<div style="display:flex;flex-direction:column;gap:12px">'
             + "".join(trade_card(t) for t in shown.head(first).to_dict("records")) + "</div>")
        # The rest only when asked (Phase 11): sending every card made this page ~500 KB for a phone.
        if len(shown) > first and st.toggle(f"Show the other {len(shown) - first} trades"):
            html('<div style="display:flex;flex-direction:column;gap:12px">'
                 + "".join(trade_card(t) for t in shown.iloc[first:].to_dict("records")) + "</div>")
        st.caption("Value received = points over a replacement-level starter at the same position, in the weeks the "
                   "team started what it got (regular season + playoff games), until each player left that roster "
                   "(kept seasons included). 'Still counting' = a player is still on that roster or a pick is still "
                   "to be drafted.")

# --- report cards ------------------------------------------------------------------------------
pair_rows = []                                        # (team, partner, trade) for every pair of teams in a trade
for r in tf.itertuples():
    for a_ in r.teams:
        for b_ in r.teams:
            if a_ != b_:
                pair_rows.append((a_, b_, r.transaction_id))
pairs = pd.DataFrame(pair_rows, columns=["team", "partner", "transaction_id"])
flow = af[af["sender_code"].notna()].groupby(["receiver_code", "sender_code"])["value"].sum()   # value got from whom


def pair_net(a_, b_):
    return float(flow.get((a_, b_), 0.0)) - float(flow.get((b_, a_), 0.0))


TEAMS_IN = dict(zip(trades["transaction_id"], trades["teams"]))   # transaction_id -> its teams


def trade_ref(row):
    """'+180.8 vs SDP, 2022' as a link to the trade. row: a v_trade_side row as a dict."""
    partners = [c for c in TEAMS_IN[row["transaction_id"]] if c != row["franchise_code"]]
    return (link(trade_url(row["transaction_id"]), signed(row["net"], 1))
            + f' <span class="muted small">vs {esc(" / ".join(city(c) for c in partners))}, {int(row["season"])}</span>')


with cards_tab:
    if sf.empty:
        st.info("No completed trades in this selection.")
    else:
        cards = []
        for code, g in sf.groupby("franchise_code"):
            mine = pairs[pairs["team"] == code]
            counts = mine.groupby("partner")["transaction_id"].nunique()
            fav = counts[counts == counts.max()].index.tolist() if not counts.empty else []
            best, worst = g.loc[g["net"].idxmax()].to_dict(), g.loc[g["net"].idxmin()].to_dict()
            cards.append({"code": code, "trades": len(g), "won": int((g["result"] == "won").sum()),
                          "lost": int((g["result"] == "lost").sum()), "even": int((g["result"] == "even").sum()),
                          "net": float(g["net"].sum()), "per": float(g["net"].mean()), "best": best, "worst": worst,
                          "fav": fav, "fav_n": int(counts.max()) if fav else 0})
        rc = pd.DataFrame(cards).sort_values("net", ascending=False)
        section("Report cards", "Every team's trades, sorted by total net value")
        html(tight(html_table(
            [("Team", "l"), ("Trades", "r"), ("W-L-E", "r"), ("Net value", "r"), ("Per trade", "r")],
            [[team(r.code, art_season), str(r.trades), f"{r.won}-{r.lost}-{r.even}",
              f'<span class="big-n">{signed(r.net, 1)}</span>', signed(r.per, 1)] for r in rc.itertuples()])))
        section("Best and worst", "Click a number to open the trade")
        html(tight(html_table(
            [("Team", "l"), ("Best trade", "l"), ("Worst trade", "l"), ("Favorite partner", "l")],
            [[team(r.code, art_season), trade_ref(r.best),
              trade_ref(r.worst) if r.trades > 1 else '<span class="muted">-</span>',
              (" / ".join(esc(city(c)) for c in r.fav) + f' <span class="muted small">({r.fav_n} trades)</span>')
              if r.fav else ""] for r in rc.itertuples()])))
        rc["franchise"] = rc["code"].map(full_name)
        lim = max(abs(rc["net"]).max(), 1)
        bars = alt.Chart(rc[["franchise", "net", "trades"]]).mark_bar(cornerRadiusEnd=4, height={"band": 0.7}).encode(
            x=alt.X("net:Q", title="Net value from trades (points over replacement)", scale=alt.Scale(domain=[-lim, lim])),
            y=alt.Y("franchise:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
            color=alt.Color("net:Q", scale=alt.Scale(domain=[-lim, 0, lim], range=[DIVERGING[0], MUTED, DIVERGING[2]],
                                                     interpolate="rgb"), legend=None),
            tooltip=[alt.Tooltip("franchise:N", title="Team"), alt.Tooltip("net:Q", title="Net value", format="+.1f"),
                     alt.Tooltip("trades:Q", title="Trades")])
        rule = alt.Chart().mark_rule(color=MUTED, strokeWidth=1).encode(x=alt.datum(0))
        show_chart((bars + rule).properties(height=30 * len(rc)))
        st.caption("W-L-E = won-lost-even: came out at least 1 point ahead / behind, or within 1. Net value adds up every trade's net, "
                   "including value from later seasons for players who were kept. Blue = gained from trading, "
                   "red = gave away more than it got.")

# --- partners ------------------------------------------------------------------------------------
with partners_tab:
    if pairs.empty:
        st.info("No completed trades in this selection.")
    else:
        section("Trade partners", "Read across: how often each row's team traded with the column's team, and how it went")
        cells = pairs.groupby(["team", "partner"])["transaction_id"].nunique().reset_index(name="trades")
        cells["net"] = [pair_net(r.team, r.partner) for r in cells.itertuples()]
        cells["Team"] = cells["team"].map(short_name)
        cells["Partner"] = cells["partner"]
        cells["team_full"], cells["partner_full"] = cells["team"].map(full_name), cells["partner"].map(full_name)
        lim = max(abs(cells["net"]).max(), 1)
        cells["ink"] = [text_on(mix(DIVERGING, (v / lim + 1) / 2)) for v in cells["net"]]
        teams_in = sorted(set(cells["team"]), key=short_name)
        x = alt.X("Partner:N", sort=teams_in, title=None,
                  axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False))
        y = alt.Y("Team:N", sort=[short_name(c) for c in teams_in], title=None,
                  axis=alt.Axis(ticks=False, domain=False, labelLimit=160))
        heat = alt.Chart(cells).mark_rect(stroke=BG, strokeWidth=2, cornerRadius=3).encode(
            x=x, y=y,
            color=alt.Color("net:Q", scale=alt.Scale(domain=[-lim, 0, lim], range=DIVERGING, interpolate="rgb"),
                            legend=alt.Legend(title="Net value", format="+.0f", orient="bottom", gradientLength=240)),
            tooltip=[alt.Tooltip("team_full:N", title="Team"), alt.Tooltip("partner_full:N", title="Partner"),
                     alt.Tooltip("trades:Q", title="Trades"), alt.Tooltip("net:Q", title="Net value", format="+.1f")])
        text = alt.Chart(cells).mark_text(fontSize=12, fontWeight=600).encode(
            x=x, y=y, text="trades:Q", color=alt.condition(alt.datum.ink == BG, alt.value(BG), alt.value(TEXT)))
        show_chart((heat + text).properties(height=max(34 * len(teams_in), 120)))
        st.caption("Number = trades between the two teams. Color = the row team's net value in them (blue = it came "
                   "out ahead, red = behind); only what moved between those two teams counts, so 3-team trades split "
                   "fairly. Hover a cell for details.")
        with st.expander("Show as a table"):
            tbl = cells.sort_values(["trades", "net"], ascending=[False, False])
            html(tight(html_table([("Team", "l"), ("Partner", "l"), ("Trades", "r"), ("Net value", "r")],
                            [[team(r.team, art_season), team(r.partner, art_season), str(int(r.trades)),
                              signed(r.net, 1)] for r in tbl.itertuples()], compact=True)))

# --- timeline ------------------------------------------------------------------------------------
with time_tab:
    td = sf if team_choice == "All teams" else sf[sf["franchise_code"] == team_choice]
    if td.empty:
        st.info("No completed trades in this selection.")
    else:
        section("Trade timeline", "Every trade, by team. Click a dot to open the trade")
        dots = td[["transaction_id", "season", "week", "created_at", "franchise_code", "net", "result"]].copy()
        dots["When"] = pd.to_datetime(dots["created_at"], utc=True).dt.tz_localize(None)
        dots["Team"] = dots["franchise_code"].map(short_name)
        dots["team_full"] = dots["franchise_code"].map(full_name)
        dots["partners"] = [" / ".join(full_name(c) for c in TEAMS_IN[tid] if c != code)
                            for tid, code in zip(dots["transaction_id"], dots["franchise_code"])]
        dots["url"] = dots["transaction_id"].map(trade_url)
        dots["Result"] = dots["result"].str.capitalize()
        dots["size"] = dots["net"].abs()
        rows = sorted(set(dots["Team"]))
        pad = pd.Timedelta(days=120)                      # keeps the first dots clear of the team names
        chart = alt.Chart(dots).mark_circle(opacity=0.95, stroke=BG, strokeWidth=1.5).encode(
            x=alt.X("When:T", title=None, scale=alt.Scale(domain=[(dots["When"].min() - pad).isoformat(),
                                                                  (dots["When"].max() + pad).isoformat()]),
                    axis=alt.Axis(format="%Y", tickCount="year", labelAngle=0)),
            y=alt.Y("Team:N", sort=rows, title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=160)),
            color=alt.Color("Result:N", scale=alt.Scale(domain=["Won", "Even", "Lost"],
                                                        range=[SERIES_BLUE, MUTED, NEG_RED]),
                            legend=alt.Legend(title=None, orient="bottom", symbolOpacity=1)),
            size=alt.Size("size:Q", scale=alt.Scale(range=[40, 520]), legend=None),   # explained in the caption
            href="url:N",
            tooltip=[alt.Tooltip("team_full:N", title="Team"), alt.Tooltip("partners:N", title="With"),
                     alt.Tooltip("season:Q", title="Season", format="d"), alt.Tooltip("week:Q", title="Week"),
                     alt.Tooltip("Result:N"), alt.Tooltip("net:Q", title="Net value", format="+.1f")])
        show_chart(chart.properties(height=max(38 * len(rows), 110)))
        st.caption("One dot per team per trade. Blue = came out ahead (won), red = behind (lost), gray = even; "
                   "bigger dots = more points won or lost. Use the Team filter above to see one team's trades.")

footer()
