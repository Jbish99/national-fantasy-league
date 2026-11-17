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
