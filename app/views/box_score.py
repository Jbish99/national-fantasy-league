"""
app/views/box_score.py
Phase 7a: the box score for any league game. The official result as the big matchup card, then both
starting lineups side by side, slot by slot (like Sleeper's matchup screen), each player with his points
and real NFL stat line; then both benches. Lineups are Sleeper's (2021 on); 2020 was on ESPN.

Open a game straight from a link: boxscore?game=<game_id> (lib.box_url()).
"""

import pandas as pd
import streamlit as st

from lib import (box_url, chip, city, esc, footer, full_name, game_label, hero, html, nfl_game_text, ordinal,
                 player_url, position_chip, pts, query, record, records_through, require, score_tile, section,
                 stat_cell, team_games, text, tiles)

require("core.v_player_game_log")

games = team_games()
one_side = games[games["franchise_code"] < games["opponent_code"]]      # one row per game (team1 < team2)
POSTSEASON_ORDER = {"Fantasy Bowl": 0, "AFC Championship": 1, "NFC Championship": 1, "2nd Round": 2, "1st Round": 3}


def featured(week_games):
    """The game to open first: Game of the Week, else the biggest postseason game, else the closest one."""
    ranked = week_games.assign(
        gotw=~week_games["is_game_of_week"].fillna(False).astype(bool),
        stage=week_games.apply(lambda g: POSTSEASON_ORDER.get(g["designation"], 4) if g["game_type"] == "playoff"
                               else 5 if g["game_type"] == "regular" else 6, axis=1),
        close=week_games["margin"].abs().fillna(999))
    return ranked.sort_values(["gotw", "stage", "close", "game_id"]).iloc[0]["game_id"]


# --- pick a game: season, week, game -------------------------------------------------------------
wanted = st.query_params.get("game")
if wanted not in set(one_side["game_id"]):
    latest = int(one_side["season"].max())
    last_week = int(one_side.loc[one_side["season"] == latest, "week"].max())
    wanted = featured(one_side[(one_side["season"] == latest) & (one_side["week"] == last_week)])
start = one_side[one_side["game_id"] == wanted].iloc[0]

c1, c2, c3 = st.columns([1, 1.3, 2.7])
season_list = sorted(one_side["season"].unique().tolist(), reverse=True)
season = c1.selectbox("Season", season_list, index=season_list.index(int(start["season"])))
in_season = one_side[one_side["season"] == season]
week_list = sorted(in_season["week"].unique().tolist())
post_weeks = set(in_season.loc[in_season["game_type"] != "regular", "week"])
week_default = int(start["week"]) if season == int(start["season"]) else max(week_list)
week = c2.selectbox("Week", week_list, index=week_list.index(week_default),
                    format_func=lambda w: f"Week {w}" + (" (postseason)" if w in post_weeks else ""))
week_games = in_season[in_season["week"] == week]
order = week_games.assign(gotw=~week_games["is_game_of_week"].fillna(False).astype(bool)).sort_values(
    ["game_type", "gotw", "game_id"], key=lambda c: c.map({"playoff": 0, "regular": 1, "consolation": 2})
    if c.name == "game_type" else c)
game_ids = order["game_id"].tolist()
game_default = wanted if wanted in game_ids else featured(week_games)


def option(game_id):
    g = week_games[week_games["game_id"] == game_id].iloc[0]
    tag = "" if g["game_type"] == "regular" else f"  ·  {game_label(g)}"
    return (f"{city(g['franchise_code'])} {pts(g['points_for'], season)}  -  "
            f"{city(g['opponent_code'])} {pts(g['points_against'], season)}{tag}")


game_id = c3.selectbox("Game", game_ids, index=game_ids.index(game_default), format_func=option)
st.query_params["game"] = game_id

# --- the result ----------------------------------------------------------------------------
g = query("select * from core.game where game_id = %s", (game_id,)).iloc[0]
season, week = int(g["season"]), int(g["week"])
a, b = g["team1_code"], g["team2_code"]
pa, pb = g["team1_points"], g["team2_points"]
if g["winner_code"] == b:                                               # winner on the left
    a, b, pa, pb = b, a, pb, pa
postseason = g["game_type"] != "regular"

if postseason:
    standings = query("select franchise_code, wins, losses, ties, regular_season_rank from core.v_season_standings "
                      "where season = %s", (season,))
    seeds = {r.franchise_code: f"{record(r.wins, r.losses, r.ties)}, {ordinal(r.regular_season_rank)} seed"
             for r in standings.itertuples()}
else:
    seeds = records_through(season, week)

label = game_label(g) if postseason else ("Game of the week" if g["is_game_of_week"] else "")
extra = ""
if g["designation"] == "IST Game":
    extra += chip("In-Season Tournament", "plain")
if isinstance(g["international_location"], str) and g["international_location"]:
    extra += chip(g["international_location"], "plain")
if g.get("is_provisional"):
    extra += chip("Provisional", "plain")
bowl = g["designation"] == "Fantasy Bowl"
co_champs = bowl and pd.isna(g["winner_code"])
html(hero({"code": a, "score": pa, "season": season, "record": seeds.get(a, "")},
          {"code": b, "score": pb, "season": season, "record": seeds.get(b, "")},
          label=label, sublabel=f"{season}, week {week}", extra=extra,
          note_left=chip("Champion") if bowl and g["winner_code"] == a else chip("Co-champion") if co_champs else "",
          note_right=chip("Co-champion") if co_champs else "",
          winner="auto" if pd.notna(g["winner_code"]) else None))
if isinstance(g["result_note"], str) and g["result_note"]:
    st.caption(g["result_note"])

# --- lineups ---------------------------------------------------------------------------------
log = query("select * from core.v_player_game_log where season = %s and week = %s and franchise_code in (%s, %s)",
            (season, week, a, b))
if log.empty:
    platform = query("select platform from core.season where season = %s", (season,))
    if not platform.empty and platform.iloc[0]["platform"] == "espn":
        st.info(f"{season} was played on ESPN. Only the final scores are on file, so there are no lineups to show.")
    else:
        st.info("Sleeper has no lineups on file for this game.")
    footer()
    st.stop()

best = query("select franchise_code, player_id, in_best_lineup from core.v_lineup_players "
             "where season = %s and week = %s and franchise_code in (%s, %s)", (season, week, a, b))
log = log.merge(best, on=["franchise_code", "player_id"], how="left")
slots = query("select slot_index, slot from core.roster_slot where season = %s and slot <> 'BN' order by slot_index",
              (season,))
week_totals = query("select franchise_code, points, custom_points from core.team_week "
                    "where season = %s and week = %s and franchise_code in (%s, %s)", (season, week, a, b))
lineup_week = query("select franchise_code, best_points, points_left_on_bench from core.v_lineup_week "
                    "where season = %s and week = %s and franchise_code in (%s, %s)", (season, week, a, b))
sleeper_total = dict(zip(week_totals["franchise_code"], week_totals["points"]))
best_total = dict(zip(lineup_week["franchise_code"], lineup_week["best_points"]))
side = {code: log[log["franchise_code"] == code] for code in (a, b)}


def short(r):
    """'J. Jennings' for phones; a defense keeps its nickname ('Steelers')."""
    name = text(r["player_name"])
    first, _, last = name.partition(" ")
    if r["position"] == "DEF":
        return name.split(" ")[-1]
    return f"{first[0]}. {last}" if first and last else name


def player_cell(r, where):
    if r is None:
        return f'<div class="bp {where}"><span class="empty">Empty</span></div>'
    game = f'{r["nfl_team"]} {nfl_game_text(r)}' if isinstance(r["nfl_team"], str) else ""
    meta = " · ".join(x for x in (text(r["position"]), game) if x)
    return (f'<div class="bp {where}"><a class="pn lnk" href="{esc(player_url(r["player_id"]))}">'
            f'<span class="full">{esc(r["player_name"])}</span><span class="short">{esc(short(r))}</span></a>'
            f'<span class="pm">{esc(meta)}</span>'
            f'<span class="ps">{stat_cell(r)}</span></div>')


def points_cell(r, other, where, bench=False):
    if r is None:
        return f'<div class="bpts {where} lo">-</div>'
    value = r["league_points"]
    lower = other is not None and other["league_points"] > value
    flag = r.get("in_best_lineup")
    star = bench and flag is not None and not pd.isna(flag) and bool(flag)
    cls = " best" if star else (" lo" if lower else "")
    mark = '<span title="Belonged in the best possible lineup">*</span>' if star else ""
    return f'<div class="bpts {where}{cls}">{pts(value)}{mark}</div>'


def row(left, right, slot, bench=False):
    return (f'<div class="brow">{player_cell(left, "l")}{points_cell(left, right, "l", bench)}'
            f'<div class="bslot">{position_chip(slot)}</div>'
            f'{points_cell(right, left, "r", bench)}{player_cell(right, "r")}</div>')


def at_slot(code, index):
    hit = side[code][side[code]["slot_index"] == index]
    return hit.iloc[0].to_dict() if len(hit) else None


def total_row(label, left_value, right_value, left_note="", right_note=""):
    def cell(value):
        return pts(value) if value is not None and not pd.isna(value) else "-"
    return (f'<div class="brow total"><div class="bp l">{esc(label)}<span class="pm">{esc(left_note)}</span></div>'
            f'<div class="bpts l">{cell(left_value)}</div><div></div><div class="bpts r">{cell(right_value)}</div>'
            f'<div class="bp r">{esc(label)}<span class="pm">{esc(right_note)}</span></div></div>')


POS_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DEF": 5}


def bench_of(code):
    df = side[code][~side[code]["is_starter"]]
    df = df.assign(o=df["position"].map(POS_ORDER).fillna(9)).sort_values(["o", "league_points"], ascending=[True, False])
    return [r for r in df.to_dict("records")]


def note(code):
    value = best_total.get(code)
    return f"Best possible {pts(value)}" if value is not None and not pd.isna(value) else ""


head = (f'<div class="brow head"><div>{esc(full_name(a))}</div><div></div><div></div><div></div>'
        f'<div class="r">{esc(full_name(b))}</div></div>')
starters = [row(at_slot(a, s.slot_index), at_slot(b, s.slot_index), s.slot) for s in slots.itertuples()]
bench_a, bench_b = bench_of(a), bench_of(b)
bench = [row(bench_a[i] if i < len(bench_a) else None, bench_b[i] if i < len(bench_b) else None, "BN", bench=True)
         for i in range(max(len(bench_a), len(bench_b)))]
starter_sum = {code: side[code].loc[side[code]["is_starter"], "league_points"].sum() for code in (a, b)}
bench_sum = {code: side[code].loc[~side[code]["is_starter"], "league_points"].sum() for code in (a, b)}

section("Box score", "Starters slot by slot, then the bench")
html(f'<section class="card box" aria-label="Box score">{head}{"".join(starters)}'
     f'{total_row("Starters", starter_sum[a], starter_sum[b], note(a), note(b))}'
     f'<div class="bdiv">Bench</div>{"".join(bench)}'
     f'{total_row("Bench", bench_sum[a], bench_sum[b])}</section>')

notes = ["Points are Sleeper's for each player. Stat lines and opponents are the real NFL games (nflverse). "
         "Gold bench points with * = he belonged in the best possible lineup."]
for code, official in ((a, pa), (b, pb)):
    total = sleeper_total.get(code)
    if total is None or official is None or pd.isna(official) or abs(float(total) - float(official)) < 0.01:
        continue
    why = ("the official score comes from the league workbook" if g["points_source"] == "sheet"
           else "the official result was set by hand")
    notes.append(f"{full_name(code)}: official score {pts(official, season)} ({why}); "
                 f"its players add up to {pts(total)} in Sleeper.")
if season == 2022 and week == 17:
    notes.append("Bills and Bengals players: their game was cancelled after Damar Hamlin's injury. Sleeper kept "
                 "the points from the part that was played, but the NFL's official stats have none, so they show "
                 "'No stats recorded'.")
if (slots["slot"] == "FLEX").any():
    empty_everywhere = query("select slot, season_slots, usable_slots from core.v_lineup_slots_week "
                             "where season = %s and week = %s and usable_slots < season_slots", (season, week))
    for r in empty_everywhere.itertuples():
        notes.append(f"Every team's lineup was missing a {r.slot} spot this week in Sleeper's data "
                     f"({r.usable_slots} of {r.season_slots} filled), so 'best possible' uses {r.usable_slots}.")
for n in notes:
    st.caption(n)

# --- the rest of the week ---------------------------------------------------------------------
others = order[order["game_id"] != game_id]
if len(others):
    section(f"Week {week} games")
    records = records_through(season, week) if not postseason else {}
    cards = []
    for o in others.itertuples():
        tags = chip(game_label(o._asdict()), "plain") if o.game_type != "regular" else ""
        cards.append(score_tile({"code": o.franchise_code, "score": o.points_for, "record": records.get(o.franchise_code)},
                                {"code": o.opponent_code, "score": o.points_against,
                                 "record": records.get(o.opponent_code)},
                                season, footer_left="Box score ›", tags=tags, href=box_url(o.game_id)))
    html(tiles(cards))
footer()
