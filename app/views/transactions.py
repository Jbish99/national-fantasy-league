"""
app/views/transactions.py
Waiver claims and free-agent pickups: what each team actually got out of them, and FAAB spending.
(Trades moved to their own page in Phase 9: app/views/trades.py, the Trade analyzer.)
"""

import pandas as pd
import streamlit as st

from lib import esc, footer, html, html_table, page_header, position_chip, pts, query, section, team

page_header("Waivers & FAAB", "Points are what an added player scored for his new team that season, from the week "
                              "of the move. 'As starter' counts only the weeks he was in the starting lineup. "
                              "Trades have their own page: Trade analyzer.")

acq = query(
    """
    select a.*, p.full_name, p.position
    from core.v_acquisitions a
    join core.player p on p.player_id = a.player_id
    where a.type in ('waiver', 'free_agent')
    """
)
seasons = sorted(set(acq["season"]), reverse=True)
left, _ = st.columns([1, 3])
choice = left.selectbox("Season", ["All seasons"] + [int(s) for s in seasons])
if choice != "All seasons":
    acq = acq[acq["season"] == choice]

pickups_tab, faab_tab = st.tabs(["Best pickups", "FAAB spending"])

# --- pickups ----------------------------------------------------------------------------------
with pickups_tab:
    section("Best pickups", "Waiver claims and free agents, by points as a starter")
    pickups = acq.sort_values("points_started", ascending=False).head(25)
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
                   "but cost nothing. FAAB sent in trades is listed on the Trade analyzer page.")
footer()
