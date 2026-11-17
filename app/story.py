"""
app/story.py
Scouting reports in football voice (Phase 10b): short write-ups built from each team's real numbers, for the
Team identities and Franchise pages. Jaden 2026-11-16: the league uses the dashboard for storytelling and for
roleplaying as the team owners, so every page should have a story layer, not just numbers.

EDIT THE WRITE-UPS HERE. Each label / formation / outcome has a couple of phrasings; a team gets one picked by
its season and franchise, so the same label doesn't read the same on every card. Placeholders like {v} are
filled from the data; a part in [[double brackets]] is left out when one of its placeholders has no value
(e.g. no traded-for player to name). A phrasing starting with "~" is a backup, used only when no other
phrasing can be filled. Every claim must be true from the numbers: "Nobody ... more" lines only appear when the
team really was No. 1 in the league that season.

Facts used (label codes from core.identity, formation codes from core.formation):
  {v} / {a}       the label's measure for the team / the league average, formatted like the evidence sentence
  {qb}            the team's QB with the most starts          {star1} {star2}  its 2 best starters
  {lead}          its best starter from the label's roster source (drafted / kept / traded for / picked up)
  {weeks}         "9 of 14 weeks" (or "every week") in the formation   {mix}  e.g. "Pro Set 7 weeks, Heavy Set 6"
  {mvp} {mvp_pts} its best starter and his points             {rec} {rank}  record and regular-season place
"""

import re
import zlib
from pathlib import Path

import pandas as pd

from lib import identity_value, ordinal, record

LOADED_MTIME = Path(__file__).stat().st_mtime    # streamlit_app.py reloads this module when the file is newer

# --- scheme (core.identity kind 'style'): how the team scored ----------------------------------
SCHEME = {
    "air_raid": [
        "Spread it out and let it fly: {v} of the offense came through the air (league average {a}).",
        "Lived through the air: passing and catching made up {v} of the offense, against a league average of {a}.",
    ],
    "ground_and_pound": [
        "Three yards and a cloud of dust: {v} of the offense came on the ground (league average {a}).",
        "Ran the ball and dared anyone to stop it: {v} of the offense on the ground, against a league average of {a}.",
    ],
    "gunslinger": [
        "The quarterback had the keys: the QB spot scored {v} of the points (league average {a})"
        "[[, with {qb} under center most weeks]].",
        "Went as far as the quarterback could carry it: {v} of the points from the QB spot (league average {a})"
        "[[, mostly {qb}]].",
    ],
    "stars_and_scrubs": [
        "Two stars and a supporting cast: {star1} and {star2} scored {v} of the points (league average {a}).",
        "Rode its two stars, {star1} and {star2}: together {v} of the points, against a league average of {a}.",
        "~Two stars and a supporting cast: its top two starters scored {v} of the points (league average {a}).",
    ],
    "balanced_attack": [
        "Everybody eats: the top two starters scored just {v} of the points (league average {a}).",
        "No one carried this offense alone: its top two starters scored only {v} of the points, against a league "
        "average of {a}.",
    ],
    "steady_eddie": [
        "Ball-control football: weekly scores swung only ±{v} around its average (league ±{a}).",
        "You knew what you were getting every Sunday: scores swung just ±{v} around its average (league ±{a}).",
    ],
    "boom_or_bust": [
        "Lived by the big play: weekly scores swung ±{v} around its average (league ±{a}), with huge weeks and duds.",
        "Boom one Sunday, bust the next: scores swung ±{v} around its average (league ±{a}).",
    ],
    "well_rounded": [
        "No one trick on offense: nothing about how it scored stood out from the league.",
        "A little of everything on offense: no single way of scoring stood out from the league.",
    ],
}

# --- front office (core.identity kind 'build'): how the roster was put together ------------------
FRONT_OFFICE = {
    "draft_purist": [
        "Built through the draft: {v} of the points came from that year's draft class (league average {a})"
        "[[, led by {lead}]].",
        "Drafted and developed: that year's draft class scored {v} of the points (league average {a})"
        "[[, with {lead} leading the way]].",
    ],
    "keeper_core": [
        "Kept the core together: keepers scored {v} of the points (league average {a})[[, led by {lead}]].",
        "The franchise tags paid off: {v} of the points came from keepers (league average {a})"
        "[[, none bigger than {lead}]].",
    ],
    "trade_shark": [
        "Always working the phones: {v} of the points came from players it traded for (league average {a})"
        "[[, headlined by {lead}]].",
        "Wheeled and dealt its way to a lineup: trade acquisitions scored {v} of the points (league average {a})"
        "[[, led by {lead}]].",
    ],
    "waiver_wire_warrior": [
        "Found gold on the waiver wire: pickups scored {v} of the points (league average {a})"
        "[[, none bigger than {lead}]].",
        "Lived on the waiver wire: {v} of the points came from pickups (league average {a})[[, led by {lead}]].",
    ],
    "youth_movement": [
        "Young legs everywhere: starters averaged {v} old (league average {a}).",
        "Built for the future: the average starter was {v} old (league average {a}).",
    ],
    "old_guard": [
        "Win-now veterans: starters averaged {v} old (league average {a}).",
        "Experience over upside: the average starter was {v} old (league average {a}).",
    ],
    "deep_bench": [
        "Depth for days: {v} bench points a week (league average {a}).",
        "Plenty in reserve: the bench put up {v} points a week (league average {a}).",
    ],
    "mixed_build": [
        "No one blueprint in the front office: the draft, keepers, trades and pickups all chipped in.",
        "The front office mixed and matched: no single way of adding talent stood out.",
    ],
}

# Which roster source names the {lead} player for a front-office label
LEAD_SOURCE = {"draft_purist": ("drafted",), "keeper_core": ("kept",), "trade_shark": ("trade",),
               "waiver_wire_warrior": ("waiver", "free_agent")}

# Added when the team was No. 1 in the league that season on the label's measure (ties included)
NUMBER_ONE = {
    "air_raid": "Nobody threw it around more.", "ground_and_pound": "Nobody ran it more.",
    "gunslinger": "No team leaned on its QB more.", "stars_and_scrubs": "Nobody leaned harder on two players.",
    "balanced_attack": "Nobody spread it around more.", "steady_eddie": "Nobody was steadier.",
    "boom_or_bust": "Nobody swung bigger.", "draft_purist": "Nobody got more from its draft class.",
    "keeper_core": "Nobody got more from its keepers.", "trade_shark": "Nobody got more out of trades.",
    "waiver_wire_warrior": "Nobody got more off the wire.", "youth_movement": "Nobody started younger.",
    "old_guard": "Nobody started older.", "deep_bench": "Nobody had more firepower on the bench.",
}

# --- formation (core.formation): what it put in its FLEX spots ------------------------------------
FORMATION = {
    "four_wide": [
        "Lined up in Four-Wide Spread {weeks}, with receivers in both FLEX spots.",
        "Went four-wide {weeks}: receivers in both FLEX spots to stretch the field.",
    ],
    "pro_set": [
        "Lived in the Pro Set: a back and a receiver in the FLEX {weeks}.",
        "Kept it classic {weeks}: one back and one receiver in the FLEX.",
    ],
    "power_i": [
        "Went Power I {weeks}, with running backs in both FLEX spots.",
        "Lined up in the Power I {weeks}: four backs and a dare to stop them.",
    ],
    "heavy_set": [
        "Brought in the heavy package {weeks}: a second tight end in the FLEX.",
        "Lived in Heavy Set {weeks}, with an extra tight end in the FLEX.",
    ],
    "jumbo": [
        "Went Jumbo {weeks}: tight ends in both FLEX spots.",
        "Rolled out the Jumbo package {weeks}, with three tight ends on the field.",
    ],
    "multiple": [
        "No base set: never lined up the same way more than half the time ({mix}).",
        "Kept defenses guessing with a multiple offense: {mix}.",
    ],
}
RARE_FORMATION = "Only {ls} of the league's lineups looked like that."     # league share under 15%

# --- the bottom line -----------------------------------------------------------------------------
MVP = ["{mvp} carried the load with {mvp_pts} points.", "{mvp} was the engine: {mvp_pts} points as a starter."]
OUTCOME = {
    "champion": ["The result: {rec} and a championship.", "The payoff: {rec} and a Fantasy Bowl title."],
    "co_champion": ["The result: {rec} and a share of the title."],
    "runner_up": ["The result: {rec} and a trip to the Fantasy Bowl.",
                  "Went {rec} and made it all the way to the Fantasy Bowl."],
    "playoffs": ["The result: {rec} and a playoff berth.", "Good enough for {rec} and a playoff spot."],
    "missed": ["The result: {rec} and an early offseason.", "It added up to {rec} and no playoffs."],
    "in_progress": ["So far: {rec}, {rank} in the standings."],
}

# --- all-time ("the book on them") -----------------------------------------------------------------
BOOK_SCHEME = {"earned": "{name} at heart: earned the label in {k} of its {n} seasons.",
               "leaned": "{name} at heart: leaned that way on average, though it never earned the label in a "
                         "single season.",
               "none": "No offensive identity has stuck: the scheme changed with the roster."}
BOOK_FORMATION = {"base": "Base formation: {name}, {share} of its weeks since {first}.",
                  "multiple": "A multiple offense over the years, most often {top} ({share} of its weeks since "
                              "{first})."}
BOOK_FRONT_OFFICE = {"earned": "In the front office, {name} at heart: earned the label in {k} of its {n} seasons.",
                     "leaned": "In the front office, {name} at heart: leaned that way on average, though it never "
                               "earned the label in a single season.",
                     "none": "The front office has never stuck to one blueprint."}
BOOK_ICON = "{icon} has scored the most regular-season points for the franchise as a starter since {first} ({pts})."


# ---------------------------------------------------------------------------------------------------
_OPTIONAL = re.compile(r"\[\[(.*?)\]\]")
_FIELD = re.compile(r"\{(\w+)\}")


def _missing(value):
    return value is None or value == "" or (isinstance(value, float) and pd.isna(value))


def fill(template, facts):
    """Fill one phrasing. Optional [[parts]] drop out when a fact is missing; None if a required one is."""
    def optional(m):
        part = m.group(1)
        return "" if any(_missing(facts.get(k)) for k in _FIELD.findall(part)) else part.format(**facts)
    text = _OPTIONAL.sub(optional, template)
    if any(_missing(facts.get(k)) for k in _FIELD.findall(text)):
        return None
    return text.format(**facts)


def pick(variants, key, facts):
    """One phrasing from the list, chosen by key (stable across reruns), skipping ones that can't be filled.
    Backups (leading '~') only when nothing else fills."""
    main = [v for v in variants if not v.startswith("~")]
    backup = [v[1:] for v in variants if v.startswith("~")]
    for group in (main, backup):
        if not group:
            continue
        start = zlib.crc32(key.encode()) % len(group)
        for i in range(len(group)):
            text = fill(group[(start + i) % len(group)], facts)
            if text:
                return text
    return ""


def _name(row):
    return row["full_name"] if isinstance(row.get("full_name"), str) else row.get("player_id")


def _pts(value):
    return f"{float(value):,.1f}"


def _rank_in_direction(season_metrics, metric, direction, value):
    """1 = the league's best that season in the label's direction (ties share the spot)."""
    vals = season_metrics.loc[season_metrics["metric"] == metric, "value"].dropna().astype(float)
    if _missing(value) or vals.empty:
        return None
    value = float(value)
    better = (vals > value + 1e-12) if direction == 1 else (vals < value - 1e-12)
    return int(better.sum()) + 1


def _player_facts(players):
    """{qb}, {star1}, {star2}, {mvp}, {mvp_pts} from a team-season's core.v_team_player_season rows."""
    facts = {}
    if players is None or players.empty:
        return facts
    per = (players.groupby(["player_id", "full_name", "position"], dropna=False)
           .agg(points=("points", "sum"), starts=("starts", "sum")).reset_index()
           .sort_values(["points", "player_id"], ascending=[False, True]))
    top = per.head(2).to_dict("records")
    if top:
        facts["mvp"], facts["mvp_pts"] = _name(top[0]), _pts(top[0]["points"])
        facts["star1"] = _name(top[0])
    if len(top) > 1:
        facts["star2"] = _name(top[1])
    qbs = per[per["position"] == "QB"].sort_values(["starts", "points"], ascending=False)
    if not qbs.empty:
        facts["qb"] = _name(qbs.iloc[0].to_dict())
    return facts


def _lead(players, code):
    """The team's best starter from the roster source a front-office label is about."""
    sources = LEAD_SOURCE.get(code)
    if not sources or players is None or players.empty:
        return None
    mine = players[players["source"].isin(sources)]
    if mine.empty:
        return None
    best = (mine.groupby(["player_id", "full_name"], dropna=False)["points"].sum().reset_index()
            .sort_values(["points", "player_id"], ascending=[False, True]).iloc[0].to_dict())
    return _name(best)


def _label_sentences(code, value, league_avg, label, season_metrics, key, facts, templates):
    """The sentence(s) for one scheme / front-office label."""
    if not code or code not in templates:
        return []
    fmt = label.get("value_format") if label else None
    f = dict(facts, v=identity_value(value, fmt), a=identity_value(league_avg, fmt))
    out = [pick(templates[code], f"{key}|{code}", f)]
    metric, direction = (label or {}).get("metric"), (label or {}).get("direction")
    if not _missing(metric) and not _missing(direction) and code in NUMBER_ONE:
        if _rank_in_direction(season_metrics, metric, int(direction), value) == 1:
            out.append(NUMBER_ONE[code])
    return [s for s in out if s]


def formation_mix(row, formation_names, unit=True):
    """'Pro Set 7 weeks, Heavy Set 6, Power I 1' from a v_team_formation / v_franchise_formation row."""
    parts = []
    for code in ("four_wide", "pro_set", "power_i", "heavy_set", "jumbo"):
        weeks = row.get(f"{code}_weeks")
        if not _missing(weeks) and int(weeks) > 0:
            parts.append((int(weeks), formation_names.get(code, code)))
    parts.sort(key=lambda p: -p[0])
    return ", ".join(f"{name} {n}{' weeks' if unit and i == 0 else ''}" for i, (n, name) in enumerate(parts))


def _formation_sentences(form, formation_names, key):
    if form is None or _missing(form.get("formation_code")) or _missing(form.get("weeks_counted")):
        return []
    code = form["formation_code"]
    n, w = (None if _missing(form.get("top_weeks")) else int(form["top_weeks"])), int(form["weeks_counted"])
    facts = {"weeks": None if n is None else ("every week" if n == w else f"{n} of {w} weeks"),
             "mix": formation_mix(form, formation_names)}
    out = [pick(FORMATION.get(code, []), f"{key}|{code}", facts)]
    ls = form.get("top_league_share")
    if not bool(form.get("is_fallback")) and not _missing(ls) and float(ls) < 0.15:
        out.append(RARE_FORMATION.format(ls=f"{float(ls):.0%}"))
    return [s for s in out if s]


def _outcome(standing, co_champion):
    if not standing or _missing(standing.get("wins")):
        return None
    if bool(standing.get("champion")):
        return "co_champion" if co_champion else "champion"
    if not _missing(standing.get("final_place")) and int(standing["final_place"]) == 2:
        return "runner_up"
    if not bool(standing.get("season_complete")) and _missing(standing.get("final_place")):
        return "in_progress"
    return "playoffs" if bool(standing.get("made_playoffs")) else "missed"


def season_report(ident, form, players, standing, season_metrics, labels, formation_names, co_champion=False):
    """The scouting report for one team-season, as plain text (escape it before putting it in HTML).
    ident: core.v_team_identity row (dict); form: core.v_team_formation row (dict or None);
    players: core.v_team_player_season rows for the team-season; standing: core.v_season_standings row plus
    season_complete; season_metrics: core.v_team_metric rows for that season (all teams);
    labels: identity_code -> core.identity row; formation_names: formation_code -> name."""
    key = f"{ident['season']}|{ident['franchise_code']}"
    facts = _player_facts(players)
    out = []
    out += _label_sentences(ident.get("style_code"), ident.get("style_value"), ident.get("style_league_avg"),
                            labels.get(ident.get("style_code")), season_metrics, key, facts, SCHEME)
    out += _formation_sentences(form, formation_names, key)
    build = ident.get("build_code")
    out += _label_sentences(build, ident.get("build_value"), ident.get("build_league_avg"), labels.get(build),
                            season_metrics, key, dict(facts, lead=_lead(players, build)), FRONT_OFFICE)
    if facts.get("mvp") and not any(facts["mvp"] in s for s in out):
        out.append(pick(MVP, f"{key}|mvp", facts))
    outcome = _outcome(standing, co_champion)
    if outcome:
        rank = standing.get("regular_season_rank")
        out.append(pick(OUTCOME[outcome], f"{key}|{outcome}",
                        {"rec": record(standing.get("wins"), standing.get("losses"), standing.get("ties")),
                         "rank": ordinal(rank) if not _missing(rank) else None}))
    return " ".join(s for s in out if s)


def franchise_book(alltime, form, players, labels):
    """'The book on them': a franchise's all-time identity in a few sentences, as plain text.
    alltime: core.v_franchise_identity row (dict); form: core.v_franchise_formation row (dict or None);
    players: every core.v_team_player_season row for the franchise; labels: identity_code -> core.identity row."""
    if not alltime or _missing(alltime.get("seasons")) or int(alltime["seasons"]) < 2:
        return ""
    first, n = int(alltime["first_season"]), int(alltime["seasons"])
    out = []
    for kind, book in (("style", BOOK_SCHEME), ("build", BOOK_FRONT_OFFICE)):
        code = alltime.get(f"{kind}_code")
        label = labels.get(code) or {}
        if _missing(code) or _missing(label.get("metric")):
            out.append(book["none"])
            continue
        k = alltime.get(f"{kind}_seasons_earned")
        k = 0 if _missing(k) else int(k)
        out.append((book["earned"] if k else book["leaned"]).format(name=alltime.get(f"{kind}_name"), k=k, n=n))
    if form and not _missing(form.get("formation_code")) and not _missing(form.get("top_share")):
        share = f"{float(form['top_share']):.0%}"
        if bool(form.get("is_fallback")):
            out.insert(1, BOOK_FORMATION["multiple"].format(top=form.get("top_name"), share=share, first=first))
        else:
            out.insert(1, BOOK_FORMATION["base"].format(name=form.get("formation_name"), share=share, first=first))
    if players is not None and not players.empty:
        best = (players.groupby(["player_id", "full_name"], dropna=False)["points"].sum().reset_index()
                .sort_values(["points", "player_id"], ascending=[False, True]).iloc[0].to_dict())
        out.append(BOOK_ICON.format(icon=_name(best), first=first, pts=f"{_pts(best['points'])} points"))
    return " ".join(out)


# ---------------------------------------------------------------------------------------------------
# Phase 12: player scouting lines (Player rankings and Players pages). One role sentence from his real usage,
# then where he finished among EVERY NFL player at his position (our league's scoring). Shares are of his
# team's carries / targets in the games he played. EDIT THE WORDING AND THE THRESHOLDS HERE.
#
# Facts: {pass_ypg} {rush_ypg} {att_pg} {pass_td} {carry_share} {target_share} {tgt_pg} {car_pg} {gl}
#        {deep_share} {rz} {fg} {fga} {fg50} {long} {sacks} {takeaways} {pa_pg} {games}
# ---------------------------------------------------------------------------------------------------
# position -> [(role, (fact, ">=" or "<=", threshold) or None)]: the first role whose test passes wins;
# None = the catch-all. A test on a missing fact fails (e.g. no snap or share data).
ROLE_RULES = {
    "QB": [("dual_threat", ("rush_ypg_v", ">=", 30)), ("volume_passer", ("att_pg_v", ">=", 37)),
           ("pocket_passer", None)],
    "RB": [("bell_cow", ("carry_share_v", ">=", 0.60)), ("receiving_back", ("target_share_v", ">=", 0.12)),
           ("goal_line_back", ("gl_pg_v", ">=", 0.6)), ("committee_back", None)],
    "WR": [("alpha_receiver", ("target_share_v", ">=", 0.25)), ("deep_threat", ("deep_share_v", ">=", 0.25)),
           ("red_zone_receiver", ("rz_pg_v", ">=", 1.0)), ("receiver", None)],
    "TE": [("featured_te", ("target_share_v", ">=", 0.18)), ("red_zone_te", ("rz_pg_v", ">=", 0.8)),
           ("tight_end", None)],
    "K": [("big_leg", ("fg50_v", ">=", 3)), ("kicker", None)],
    "DEF": [("ball_hawks", ("takeaways_pg_v", ">=", 1.7)), ("pass_rush", ("sacks_pg_v", ">=", 3)),
            ("stingy", ("pa_pg_v", "<=", 18)), ("defense", None)],
}
# Football names for the roles (shown as a label on the rankings page).
ROLE_NAMES = {
    "dual_threat": "Dual threat", "volume_passer": "Volume passer", "pocket_passer": "Pocket passer",
    "bell_cow": "Bell cow", "receiving_back": "Third-down back", "goal_line_back": "Goal-line back",
    "committee_back": "Committee back", "alpha_receiver": "Alpha receiver", "deep_threat": "Deep threat",
    "red_zone_receiver": "Red-zone target", "receiver": "Receiver", "featured_te": "Featured tight end",
    "red_zone_te": "Red-zone tight end", "tight_end": "Tight end", "big_leg": "Big leg", "kicker": "Kicker",
    "ball_hawks": "Ball hawks", "pass_rush": "Pass rush", "stingy": "Stingy defense", "defense": "Defense",
}
ROLE = {
    "dual_threat": [
        "Dual-threat quarterback: {rush_ypg} rushing yards a game on top of {pass_ypg} through the air.",
        "Makes plays with his legs: {rush_ypg} rushing yards a game to go with {pass_ypg} passing.",
    ],
    "volume_passer": [
        "Volume passer: {att_pg} throws a game for {pass_ypg} yards.",
        "Lives in the shotgun: {att_pg} pass attempts a game ({pass_ypg} yards).",
    ],
    "pocket_passer": [
        "Runs the offense from the pocket: {pass_ypg} passing yards a game[[ and {pass_td} touchdown passes]].",
        "Pocket passer: {pass_ypg} yards a game through the air[[, {pass_td} TD passes]].",
    ],
    "bell_cow": [
        "Bell-cow back: {carry_share} of his team's carries in the games he played.",
        "The workhorse: {carry_share} of his team's carries when he suited up[[, plus {tgt_pg} targets a game]].",
    ],
    "receiving_back": [
        "Pass-catching back: {target_share} of his team's targets[[ and {carry_share} of its carries]] when he played.",
        "A weapon out of the backfield: {target_share} of his team's targets in the games he played.",
    ],
    "goal_line_back": [
        "Goal-line hammer: {gl} carries inside the 5-yard line.",
        "The short-yardage call: {gl} carries inside the 5.",
    ],
    "committee_back": [
        "Committee back: {carry_share} of his team's carries in the games he played.",
        "Shared the backfield: {carry_share} of his team's carries when he played.",
        "~Running back: {car_pg} carries and {tgt_pg} targets a game.",
    ],
    "alpha_receiver": [
        "Alpha receiver: {target_share} of his team's targets in the games he played.",
        "Target hog: the ball went his way on {target_share} of his team's targets when he played.",
    ],
    "deep_threat": [
        "Field stretcher: {deep_share} of his targets came 20+ yards downfield.",
        "Deep threat: {deep_share} of his targets were thrown 20+ yards down the field.",
    ],
    "red_zone_receiver": [
        "Red-zone target: {rz} looks inside the 20.",
        "Where the ball goes near the goal line: {rz} targets inside the 20.",
    ],
    "receiver": [
        "Pass catcher: {tgt_pg} targets a game[[ ({target_share} of his team's when he played)]].",
        "On the receiving end of {tgt_pg} targets a game[[, {target_share} of his team's when he played]].",
    ],
    "featured_te": [
        "Featured tight end: {target_share} of his team's targets in the games he played.",
        "A tight end the offense runs through: {target_share} of his team's targets when he played.",
    ],
    "red_zone_te": [
        "Red-zone tight end: {rz} looks inside the 20.",
        "A big target near the goal line: {rz} looks inside the 20.",
    ],
    "tight_end": [
        "Tight end: {tgt_pg} targets a game[[ ({target_share} of his team's when he played)]].",
        "In the pattern for {tgt_pg} targets a game[[, {target_share} of his team's when he played]].",
    ],
    "big_leg": [
        "Big leg: {fg50} field goals from 50+ yards[[ (long of {long})]].",
        "Range for days: {fg50} field goals from 50 or more[[, long of {long}]].",
    ],
    "kicker": [
        "Kicker: {fg} of {fga} on field goals[[, long of {long}]].",
        "Made {fg} of {fga} field goals[[ (long of {long})]].",
    ],
    "ball_hawks": [
        "Ball hawks: {takeaways} takeaways in {games} games.",
        "A defense that takes the ball away: {takeaways} takeaways in {games} games.",
    ],
    "pass_rush": [
        "Lives in the backfield: {sacks} sacks in {games} games.",
        "A pass rush that gets home: {sacks} sacks in {games} games.",
    ],
    "stingy": [
        "Stingy defense: {pa_pg} points allowed a game.",
        "Hard to score on: {pa_pg} points allowed a game.",
    ],
    "defense": [
        "Defense: {sacks} sacks and {takeaways} takeaways in {games} games.",
        "{sacks} sacks and {takeaways} takeaways in {games} games.",
    ],
}
# Where he finished. {label} = 'RB4' (total points), {label_ppg} = 'RB2' per game (needs the minimum games).
FINISH = {
    "first": [
        "No {word} scored more in {season}: {points} points[[, {label_ppg} per game]].",
        "The top-scoring {word} of {season}: {points} points[[ ({label_ppg} per game)]].",
    ],
    "shared_first": ["Tied for the most points by a {word} in {season}: {points}."],
    "other": [
        "Finished {label} in {season} with {points} points[[, {label_ppg} per game]].",
        "{label} in {season}: {points} points[[, {label_ppg} on a per-game basis]].",
    ],
}
FINISH_ALLTIME = {
    "first": [
        "No {word} has scored more since 2021: {points} points in {games} games.",
        "The top-scoring {word} since 2021: {points} points in {games} games.",
    ],
    "shared_first": ["Tied for the most points by a {word} since 2021: {points}."],
    "other": [
        "{label} since 2021: {points} points in {games} games[[, {label_ppg} per game]].",
        "All-time {label} since 2021, with {points} points in {games} games[[ ({label_ppg} per game)]].",
    ],
}
POSITION_WORDS = {"QB": "quarterback", "RB": "running back", "WR": "wide receiver", "TE": "tight end", "K": "kicker",
                  "DEF": "defense"}
NUMBER_ONE_WEEKS = "Finished a week as the No. 1 {word} {n} times."


def _f(row, key):
    v = row.get(key)
    return None if _missing(v) else float(v)


def _per_game(row, key):
    games, v = _f(row, "games"), _f(row, key)
    return None if not games or v is None else v / games


def _share(value):
    return None if value is None else f"{value:.0%}"


def _one(value):
    return None if value is None else f"{value:.1f}"


def player_facts(row):
    """Numbers for the role sentence, from a core.v_player_season_rank / v_player_career_rank row (dict).
    *_v = raw value (for the tests), the rest = display text."""
    targets, deep = _f(row, "targets"), _f(row, "deep_targets")
    takeaways = None if _f(row, "def_interceptions") is None else \
        (_f(row, "def_interceptions") or 0) + (_f(row, "def_fumble_recoveries") or 0)
    facts = {
        "pass_ypg_v": _per_game(row, "passing_yards"), "rush_ypg_v": _per_game(row, "rushing_yards"),
        "att_pg_v": _per_game(row, "pass_attempts"), "carry_share_v": _f(row, "carry_share"),
        "target_share_v": _f(row, "target_share"), "gl_pg_v": _per_game(row, "goal_line_carries"),
        "deep_share_v": deep / targets if targets and targets >= 20 and deep is not None else None,
        "rz_pg_v": _per_game(row, "red_zone_looks"), "fg50_v": _f(row, "fg_made_50"),
        "takeaways_pg_v": None if takeaways is None or not _f(row, "games") else takeaways / _f(row, "games"),
        "sacks_pg_v": _per_game(row, "def_sacks"), "pa_pg_v": _f(row, "def_points_allowed_pg"),
    }
    facts.update({
        "pass_ypg": _one(facts["pass_ypg_v"]), "rush_ypg": _one(facts["rush_ypg_v"]), "att_pg": _one(facts["att_pg_v"]),
        "pass_td": None if not _f(row, "passing_tds") else f"{int(_f(row, 'passing_tds'))}",
        "carry_share": _share(facts["carry_share_v"]), "target_share": _share(facts["target_share_v"]),
        "tgt_pg": _one(_per_game(row, "targets")), "car_pg": _one(_per_game(row, "carries")),
        "gl": None if _f(row, "goal_line_carries") is None else f"{int(_f(row, 'goal_line_carries'))}",
        "deep_share": _share(facts["deep_share_v"]),
        "rz": None if _f(row, "red_zone_looks") is None else f"{int(_f(row, 'red_zone_looks'))}",
        "fg": None if _f(row, "fg_made") is None else f"{int(_f(row, 'fg_made'))}",
        "fga": None if _f(row, "fg_att") is None else f"{int(_f(row, 'fg_att'))}",
        "fg50": None if _f(row, "fg_made_50") is None else f"{int(_f(row, 'fg_made_50'))}",
        "long": None if not _f(row, "fg_long") else f"{int(_f(row, 'fg_long'))}",
        "sacks": None if _f(row, "def_sacks") is None else f"{_f(row, 'def_sacks'):g}",
        "takeaways": None if takeaways is None else f"{int(takeaways)}",
        "pa_pg": _one(facts["pa_pg_v"]),
        "games": None if _f(row, "games") is None else f"{int(_f(row, 'games'))}",
    })
    return facts


def player_role(row, facts=None):
    """The role code (ROLE_RULES) for a season / career row, or None for an unknown position."""
    facts = facts or player_facts(row)
    for code, test in ROLE_RULES.get(row.get("position"), []):
        if test is None:
            return code
        value = facts.get(test[0])
        if value is not None and (value >= test[2] if test[1] == ">=" else value <= test[2]):
            return code
    return None


def player_line(row, season=None):
    """A player's scouting line as plain text: role sentence + where he finished (escape it for HTML).
    row: core.v_player_season_rank row (season = its season) or core.v_player_career_rank row (season=None =
    all-time since 2021). Every number comes from the row; 'No ... more' only when he really was No. 1."""
    facts = player_facts(row)
    role = player_role(row, facts)
    key = f"{row.get('rank_id')}|{season or 'all'}"
    out = [pick(ROLE.get(role, []), f"{key}|role", facts)] if role else []
    position = row.get("position")
    rank = row.get("pos_rank")
    if not _missing(rank) and isinstance(position, str):
        word = POSITION_WORDS.get(position, position)
        tied = bool(row.get("pos_tied")) if not _missing(row.get("pos_tied")) else False
        ppg_rank = row.get("pos_rank_ppg")
        finish = {"label": f"{'T-' if tied else ''}{position}{int(rank)}", "season": season, "word": word,
                  "points": f"{float(row['points']):,.1f}", "games": f"{int(float(row['games']))}",
                  "label_ppg": None if _missing(ppg_rank) else
                  f"{'T-' if bool(row.get('pos_tied_ppg')) else ''}{position}{int(ppg_rank)}"}
        case = "other" if int(rank) != 1 else ("shared_first" if tied else "first")
        out.append(pick((FINISH if season else FINISH_ALLTIME)[case], f"{key}|finish", finish))
        top1 = row.get("top1_weeks")
        if not _missing(top1) and int(top1) >= 2:
            out.append(NUMBER_ONE_WEEKS.format(word=word, n=int(top1)))
    return " ".join(s for s in out if s)
