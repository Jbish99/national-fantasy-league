"""
app/views/transactions.py
Trades and waiver/free-agent pickups: what each team actually got out of them.
"""

import pandas as pd
import streamlit as st

from lib import (city, esc, footer, full_name, html, html_table, logo, page_header, position_chip, pts, query,
                 section, team)

page_header("Trades & waivers", "Points are what an added player scored for his new team that season, from the week "
                                "of the move. 'As starter' counts only the weeks he was in the starting lineup.")

acq = query(
    """
    select a.*, p.full_name, p.position
    from core.v_acquisitions a
    join core.player p on p.player_id = a.player_id
    """
)
# Trades come from the trade records themselves (not from players added), so a team that only
# gave something away, or a deal of only picks / FAAB, still shows up.
trades = query(
    """
    select transaction_id, season, week, created_at
    from core.transaction
    where type = 'trade' and status = 'complete'
    """
)
seasons = sorted(set(acq["season"]) | set(trades["season"]), reverse=True)
left, _ = st.columns([1, 3])
choice = left.selectbox("Season", ["All seasons"] + [int(s) for s in seasons])
if choice != "All seasons":
    acq = acq[acq["season"] == choice]
    trades = trades[trades["season"] == choice]

trades_tab, pickups_tab, faab_tab = st.tabs(["Trades", "Best pickups", "FAAB spending"])

# --- trades ---------------------------------------------------------------------------------
with trades_tab:
    moves = query("select transaction_id, franchise_code from core.transaction_player")
    picks = query("select * from core.transaction_pick")
    faab = query("select * from core.transaction_faab")
    if trades.empty:
        st.info("No completed trades in this selection.")
    cards = []
    for trade in trades.sort_values("created_at", ascending=False).itertuples():
        tid = trade.transaction_id
        deal = acq[(acq["transaction_id"] == tid)]
        deal_picks = picks[picks["transaction_id"] == tid]
        deal_faab = faab[faab["transaction_id"] == tid]
        teams = sorted(
            set(moves.loc[moves["transaction_id"] == tid, "franchise_code"])
            | set(deal_picks["from_code"]) | set(deal_picks["to_code"])
            | set(deal_faab["from_code"]) | set(deal_faab["to_code"])
        )
        if not teams:
            continue
        sides, totals = [], {}
        for code in teams:
            got = deal[deal["franchise_code"] == code]
            totals[code] = got["points_started"].sum()
            lines = [f'{position_chip(r.position)} <strong>{esc(r.full_name)}</strong> '
                     f'<span class="muted">{r.points_started:.1f} as starter, {r.points_rostered:.1f} total</span>'
                     for r in got.itertuples()]
            lines += [f"{int(p.pick_season)} round {int(p.pick_round)} pick "
                      f'<span class="muted">(originally {esc(city(p.original_code))}\'s)</span>'
                      for p in deal_picks[deal_picks["to_code"] == code].itertuples()]
            lines += [f"${int(f.amount)} FAAB" for f in deal_faab[deal_faab["to_code"] == code].itertuples()]
            items = "".join(f"<li>{line}</li>" for line in lines) or '<li class="muted">nothing</li>'
            sides.append(f'<div class="trade-side"><div class="tc">{logo(code, trade.season, 26)}'
                         f'<span>{esc(full_name(code))} got</span></div><ul>{items}</ul></div>')
        foot = ""
        if len(totals) == 2 and max(totals.values()) > 0:
            best = max(totals, key=totals.get)
            gap = abs(list(totals.values())[0] - list(totals.values())[1])
            foot = (f'<div class="trade-foot">Starter points from the players received: '
                    f'<strong>{esc(city(best))} +{gap:.1f}</strong>. Draft picks and FAAB aren\'t counted.</div>')
        cards.append(f'<article class="trade"><div class="trade-head"><span><strong style="color:#F2F5FA">'
                     f'{trade.season}, week {int(trade.week)}</strong></span>'
                     f'<span>{" / ".join(esc(city(t)) for t in teams)}</span></div>'
                     f'<div class="trade-sides">{"".join(sides)}</div>{foot}</article>')
    if cards:
        html(f'<div style="display:flex;flex-direction:column;gap:12px">{"".join(cards)}</div>')

# --- pickups ----------------------------------------------------------------------------------
with pickups_tab:
    section("Best pickups", "Waiver claims and free agents, by points as a starter")
    pickups = acq[acq["type"].isin(["waiver", "free_agent"])].sort_values("points_started", ascending=False).head(25)
    html(html_table(
        [("Player", "l"), ("Pos", "l"), ("Team", "l"), ("Season", "l"), ("Week added", "r"), ("How", "l"),
         ("FAAB", "r"), ("Points as starter", "r"), ("Weeks started", "r")],
        [[f"<strong>{esc(r.full_name)}</strong>", position_chip(r.position), team(r.franchise_code, r.season),
          str(r.season), str(int(r.week)), "Waiver" if r.type == "waiver" else "Free agent",
          "" if pd.isna(r.faab_bid) else f"${int(r.faab_bid)}", f'<span class="big-n">{r.points_started:.2f}</span>',
          str(int(r.weeks_started))] for r in pickups.itertuples()]))

# --- FAAB ---------------------------------------------------------------------------------------
with faab_tab:
    claims = acq[acq["type"] == "waiver"]
    if claims.empty:
        st.info("No waiver claims in this selection.")
    else:
        spend = claims.groupby("franchise_code").agg(
            spent=("faab_bid", "sum"), claims=("player_id", "count"), points=("points_started", "sum")).reset_index()
        spend["per_dollar"] = spend["points"] / spend["spent"].where(spend["spent"] > 0)
        spend = spend.sort_values("points", ascending=False)
        section("FAAB spending", "Starter points from waiver claims")
        html(html_table(
            [("Team", "l"), ("Claims won", "r"), ("FAAB spent", "r"), ("Starter points from claims", "r"),
             ("Points per $", "r")],
            [[team(r.franchise_code, None if choice == "All seasons" else choice, name="full"), str(int(r.claims)),
              f"${int(r.spent or 0)}", pts(r.points), "" if pd.isna(r.per_dollar) else f"{r.per_dollar:.2f}"]
             for r in spend.itertuples()]))
        st.caption("Only claims that went through are counted. Failed bids are in the database (core.transaction) "
                   "but cost nothing.")
footer()
