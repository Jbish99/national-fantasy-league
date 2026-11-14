"""
app/views/transactions.py
Trades and waiver/free-agent pickups: what each team actually got out of them.
"""

import pandas as pd
import streamlit as st

from lib import footer, full_name, query, table

st.title("Trades & waivers")
st.caption("Points are what an added player scored for his new team that season, from the week of the move. "
           "'As starter' counts only the weeks he was in the starting lineup.")

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
choice = st.selectbox("Season", ["All seasons"] + [int(s) for s in seasons])
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
        with st.container(border=True):
            st.markdown(f"**{trade.season}, week {int(trade.week)}**: " + " / ".join(full_name(t) for t in teams))
            cols = st.columns(len(teams))
            totals = {}
            for col, team in zip(cols, teams):
                got = deal[deal["franchise_code"] == team]
                totals[team] = got["points_started"].sum()
                lines = [f"{r.full_name} ({r.position}): {r.points_started:.1f} as starter, {r.points_rostered:.1f} total"
                         for r in got.itertuples()]
                lines += [f"{int(p.pick_season)} round {int(p.pick_round)} pick (originally {full_name(p.original_code)}'s)"
                          for p in deal_picks[deal_picks["to_code"] == team].itertuples()]
                lines += [f"${int(f.amount)} FAAB" for f in deal_faab[deal_faab["to_code"] == team].itertuples()]
                col.markdown(f"*{full_name(team)} got:*\n" + "\n".join(f"- {line}" for line in lines or ["nothing"]))
            if len(totals) == 2 and max(totals.values()) > 0:
                best = max(totals, key=totals.get)
                gap = abs(list(totals.values())[0] - list(totals.values())[1])
                st.caption(f"Starter points from the players received: {full_name(best)} +{gap:.1f}. "
                           "Draft picks and FAAB aren't counted.")

# --- pickups ----------------------------------------------------------------------------------
with pickups_tab:
    pickups = acq[acq["type"].isin(["waiver", "free_agent"])].sort_values("points_started", ascending=False).head(25)
    table(
        [{"Player": r.full_name, "Pos": r.position, "Team": full_name(r.franchise_code), "Season": str(r.season),
          "Week added": int(r.week), "How": "Waiver" if r.type == "waiver" else "Free agent",
          "FAAB": None if pd.isna(r.faab_bid) else int(r.faab_bid),
          "Points as starter": r.points_started, "Weeks started": int(r.weeks_started)}
         for r in pickups.itertuples()],
        column_config={"Points as starter": st.column_config.NumberColumn(format="%.2f"),
                       "FAAB": st.column_config.NumberColumn(format="$%d")},
    )

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
        table(
            [{"Team": full_name(r.franchise_code), "Claims won": int(r.claims), "FAAB spent": int(r.spent or 0),
              "Starter points from claims": r.points, "Points per $": r.per_dollar} for r in spend.itertuples()],
            column_config={"FAAB spent": st.column_config.NumberColumn(format="$%d"),
                           "Starter points from claims": st.column_config.NumberColumn(format="%.2f"),
                           "Points per $": st.column_config.NumberColumn(format="%.2f")},
        )
        st.caption("Only claims that went through are counted. Failed bids are in the database (core.transaction) "
                   "but cost nothing.")
footer()
