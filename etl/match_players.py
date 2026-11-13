"""
etl/match_players.py

Phase 2: match the player names typed in the All-In-One workbook to Sleeper player ids.
Covers the Keepers sheet, the 2020 (ESPN) draft, and the Individual Awards sheet.

How a name gets matched, most trusted first:
  1. Defenses ("Steelers", "Cowboys D/ST", "Pittsburgh Steelers") -> Sleeper's team id (PIT, DAL...).
  2. The team's own players that season (keepers also look at last season's roster and
     this season's draft). A name only has to beat the other ~30 players on that roster.
  3. Every player in Sleeper's list (the 2020 season, AFC/NFC Pro Bowl picks, or when 2 fails).

Each match gets a status:
  auto          exact name, no doubt
  check         close spelling ("Saquon Barkely") or other small doubt -> used, but worth a look
  unmatched     no good match -> NOT used until you fill in a player_id
  confirmed     you reviewed it (kept exactly as you left it on every re-run)
  not_a_player  you marked it as not a player (a coach, an owner) -> never used

Output:
  data/manual/player_name_map.csv   the review copy. Open it in Excel, fix rows, save as CSV.
  core.player_name_map              the same rows in the database (what the loaders read).

To fix a row in the CSV: put the right Sleeper player_id in player_id and change status
to confirmed. To mark a coach or a typo row as not a player: status not_a_player.
Then re-run:  python -m etl.load_core --from names

Run from the project root:
    python -m etl.match_players          # just the matching (the 'names' step of load_core)
"""

import csv
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import psycopg

from etl.common import LoadProblem, clean_name, franchise_code_for, workbook_rows
from etl.db import PROJECT_ROOT, get_connection

CSV_PATH = PROJECT_ROOT / "data" / "manual" / "player_name_map.csv"
CSV_COLUMNS = ["status", "source", "season", "team", "name_as_entered", "position", "player_id",
               "matched_name", "matched_position", "method", "score", "candidates", "note", "your_notes"]
REVIEWED = ("confirmed", "not_a_player")     # statuses you set; never overwritten
STATUS_ORDER = {"unmatched": 0, "check": 1, "auto": 2, "confirmed": 3, "not_a_player": 4}

SKIP_AWARDS = {"most valuable owner"}        # the 'Name' on these rows is a team, not a player
FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}

# Workbook position -> Sleeper positions it allows. Missing (BENCH, blank) = any position.
POSITION_GROUPS = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"},
    "DST": {"DEF"}, "DEF": {"DEF"}, "D/ST": {"DEF"},
    "FX": {"RB", "WR", "TE"}, "FLEX": {"RB", "WR", "TE"},
}

# Matching thresholds (scores run 0..1, 1 = identical after clean-up)
TEAM_MIN_SCORE = 0.80      # on the team's roster, a name must score at least this...
TEAM_MIN_GAP = 0.05        # ...and beat the next-best teammate by this much
GLOBAL_MIN_SCORE = 0.90    # across all of Sleeper's players, a close spelling must score this
GLOBAL_MIN_GAP = 0.03


# ---------------------------------------------------------------------------
# Name clean-up and similarity
# ---------------------------------------------------------------------------

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
NICKNAMES = {
    "mike": "michael", "chris": "christopher", "matt": "matthew", "josh": "joshua", "dan": "daniel",
    "danny": "daniel", "will": "william", "bill": "william", "rob": "robert", "bob": "robert",
    "tom": "thomas", "tommy": "thomas", "tony": "anthony", "jon": "jonathan", "nick": "nicholas",
    "alex": "alexander", "sam": "samuel", "ben": "benjamin", "joe": "joseph", "jim": "james",
    "jimmy": "james", "zach": "zachary", "zack": "zachary", "jake": "jacob", "pat": "patrick",
    "steve": "steven", "drew": "andrew", "andy": "andrew", "jeff": "jeffrey", "greg": "gregory",
    "ken": "kenneth", "kenny": "kenneth", "rich": "richard", "rick": "richard", "dave": "david",
    "ed": "edward", "eddie": "edward", "cam": "cameron", "gabe": "gabriel", "dj": "dj",
}


def normalize(name):
    """'Odell Beckham Jr.' -> 'odell beckham', 'D.J. Moore' -> 'dj moore', 'Amon-Ra' -> 'amon ra'."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[.'`]", "", text)
    text = re.sub(r"[-_/,()]", " ", text)
    tokens = text.split()
    while len(tokens) > 1 and tokens[-1] in SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def canonical(norm):
    """Swap a nickname first name for the full one: 'mike evans' -> 'michael evans'."""
    tokens = norm.split()
    if tokens:
        tokens[0] = NICKNAMES.get(tokens[0], tokens[0])
    return " ".join(tokens)


def similarity(a, b):
    """(score, how) for two normalized names."""
    if a == b:
        return 1.0, "exact"
    ca, cb = canonical(a), canonical(b)
    if ca == cb:
        return 0.95, "nickname"
    score, how = SequenceMatcher(None, ca, cb).ratio(), "spelling"
    ta, tb = ca.split(), cb.split()
    if len(ta) >= 2 and len(ta) == len(tb) and all(
            x.startswith(y) or y.startswith(x) for x, y in zip(ta[1:], tb[1:])):
        if ta[0] == tb[0] and score < 0.92:
            score, how = 0.92, "shortened"            # 'Odell B' vs 'Odell Beckham', 'Rodrigo B' vs 'Rodrigo Blankenship'
        elif min(len(ta[0]), len(tb[0])) >= 3 and (ta[0].startswith(tb[0]) or tb[0].startswith(ta[0])) \
                and score < 0.90:
            score, how = 0.90, "shortened"            # 'Chig Okonkwo' vs 'Chigoziem Okonkwo'
    if len(ta) >= 2 and len(tb) >= 2 and ta[-1] == tb[-1] and ta[0][0] == tb[0][0]:
        if score < 0.88:
            score, how = 0.88, "initial"              # same last name, same first initial
    if (len(ta) == 1) != (len(tb) == 1) and ta[-1] == tb[-1] and score < 0.85:
        score, how = 0.85, "last_name_only"           # 'Gay' vs 'Matt Gay'
    return round(score, 3), how


# ---------------------------------------------------------------------------
# Defenses: Sleeper's id for a defense is the team abbreviation
# ---------------------------------------------------------------------------

DEFENSE_NICKNAMES = {
    "cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF", "panthers": "CAR",
    "bears": "CHI", "bengals": "CIN", "browns": "CLE", "cowboys": "DAL", "broncos": "DEN",
    "lions": "DET", "packers": "GB", "texans": "HOU", "colts": "IND", "jaguars": "JAX",
    "chiefs": "KC", "raiders": "LV", "chargers": "LAC", "rams": "LAR", "dolphins": "MIA",
    "vikings": "MIN", "patriots": "NE", "saints": "NO", "giants": "NYG", "jets": "NYJ",
    "eagles": "PHI", "steelers": "PIT", "seahawks": "SEA", "49ers": "SF", "niners": "SF",
    "buccaneers": "TB", "bucs": "TB", "titans": "TEN", "commanders": "WAS", "redskins": "WAS",
    "football team": "WAS",
}
DEFENSE_CITIES = {
    "arizona": "ARI", "atlanta": "ATL", "baltimore": "BAL", "buffalo": "BUF", "carolina": "CAR",
    "chicago": "CHI", "cincinnati": "CIN", "cleveland": "CLE", "dallas": "DAL", "denver": "DEN",
    "detroit": "DET", "green bay": "GB", "houston": "HOU", "indianapolis": "IND",
    "jacksonville": "JAX", "kansas city": "KC", "las vegas": "LV", "miami": "MIA",
    "minnesota": "MIN", "new england": "NE", "new orleans": "NO", "philadelphia": "PHI",
    "pittsburgh": "PIT", "seattle": "SEA", "san francisco": "SF", "tampa bay": "TB",
    "tennessee": "TEN", "washington": "WAS",
}
DEFENSE_WORDS = re.compile(r"\b(d st|dst|def|defense|d)\b")
TEAM_WORDS = {word for words in [*DEFENSE_NICKNAMES, *DEFENSE_CITIES, "new york", "los angeles"]
              for word in words.split()}


def defense_abbr(name):
    """'Cowboys D/ST' -> 'DAL', 'Pittsburgh Steelers' -> 'PIT', 'Colts' -> 'IND'. None if not a team."""
    text = " " + re.sub(r"\s+", " ", DEFENSE_WORDS.sub(" ", normalize(name))).strip() + " "
    for words, abbr in DEFENSE_NICKNAMES.items():
        if f" {words} " in text:
            return abbr
    for words, abbr in DEFENSE_CITIES.items():
        if text.strip() == words:
            return abbr
    return None


def looks_like_defense(name, positions):
    """True for 'Colts' / 'Cowboys D/ST' / 'Pittsburgh Steelers', or anything typed as DST."""
    if positions & {"DST", "DEF", "D/ST"}:
        return True
    if re.search(r"d\s*/\s*st\b|\bdst\b|\bdefense\b", str(name), re.IGNORECASE):
        return True
    if positions - {"BENCH"}:
        return False                      # typed as QB/RB/WR/... -> a person
    words = DEFENSE_WORDS.sub(" ", normalize(name)).split()
    return bool(words) and all(w in TEAM_WORDS for w in words) and defense_abbr(name) is not None


# ---------------------------------------------------------------------------
# What we're matching, and what we're matching against
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    source: str            # keeper / award / draft
    season: int
    team_code: str | None
    name: str              # as entered (spaces cleaned)
    positions: set = field(default_factory=set)

    @property
    def position(self):
        """The most specific position typed for this name (RB beats FX beats BENCH/blank)."""
        specific = sorted(p for p in self.positions if p in POSITION_GROUPS and p not in ("FX", "FLEX"))
        if specific:
            return specific[0]
        return "FX" if self.positions & {"FX", "FLEX"} else (sorted(self.positions)[0] if self.positions else "")

    @property
    def allowed(self):
        """Sleeper positions this name may be, from every position typed for it. None = any."""
        groups = [POSITION_GROUPS[p] for p in self.positions if p in POSITION_GROUPS]
        return set().union(*groups) if groups else None


@dataclass(eq=False)          # one object per player id, compared by identity (so it can sit in sets)
class Player:
    player_id: str
    name: str
    norm: str
    positions: set
    rookie_year: int | None
    search_rank: int
    in_league: bool        # ever on a roster, drafted or moved in this league


@dataclass
class Match:
    player: Player | None
    method: str
    score: float | None
    status: str
    candidates: list = field(default_factory=list)   # [(score, how, Player)] alternatives
    note: str = ""


def collect_entries(conn):
    entries = {}

    def add(source, season, team_code, name, position):
        name = clean_name(name)
        if not name:
            return
        entry = entries.setdefault((source, season, team_code, name), Entry(source, season, team_code, name))
        if position:
            entry.positions.add(str(position).strip().upper())

    problems = []
    for row_number, d in workbook_rows(conn, "Keepers"):
        season = int(d["Year"])
        code = franchise_code_for(d.get("Team"), season)
        if not code:
            problems.append(f"Keepers row {row_number}: don't recognize team '{d.get('Team')}'")
            continue
        add("keeper", season, code, d.get("Name"), d.get("Position"))

    for row_number, d in workbook_rows(conn, "Individual Awards"):
        if str(d.get("Award") or "").strip().lower() in SKIP_AWARDS:
            continue
        season = int(d["Year"])
        team = str(d.get("Team") or "").strip()
        code = None if team.upper() in ("", "AFC", "NFC") else franchise_code_for(team, season)
        if team and team.upper() not in ("AFC", "NFC") and not code:
            problems.append(f"Individual Awards row {row_number}: don't recognize team '{team}'")
            continue
        add("award", season, code, d.get("Name"), d.get("Position"))

    sheet_seasons = {s for (s,) in conn.execute("select season from core.season where platform <> 'sleeper'")}
    for row_number, d in workbook_rows(conn, "Draft"):
        season = int(d["Year"])
        if season not in sheet_seasons:
            continue                      # Sleeper has the real draft for these seasons
        team = str(d.get("Drafting Team") or "").strip()
        code = None if team in ("", "?") else franchise_code_for(team, season)
        if team not in ("", "?") and not code:
            problems.append(f"Draft row {row_number}: don't recognize team '{team}'")
            continue
        add("draft", season, code, d.get("Player Name"), d.get("Position"))

    if problems:
        raise LoadProblem("names", problems)
    return entries


def fetch_players(conn):
    seen = {pid for (pid,) in conn.execute(
        """
        select player_id from core.player_week
        union select player_id from core.draft_pick dp join core.draft d using (draft_id)
              where d.source = 'sleeper' and player_id is not null
        union select player_id from core.transaction_player
        """)}
    ranks = dict(conn.execute(
        """
        select p.key, (p.value ->> 'search_rank')::int
        from raw.v_sleeper_latest r, jsonb_each(r.payload) p
        where r.endpoint = 'players' and jsonb_typeof(p.value -> 'search_rank') = 'number'
        """).fetchall())
    players = {}
    for pid, name, position, fantasy_positions, rookie_year in conn.execute(
            "select player_id, full_name, position, fantasy_positions, rookie_year from core.player "
            "where full_name is not null"):
        positions = set(fantasy_positions or []) | ({position} if position else set())
        players[pid] = Player(pid, name, normalize(name), positions, rookie_year,
                              ranks.get(pid) or 9_999_999, pid in seen)
    return players


def team_pools(conn, players):
    """(season, franchise_code) -> players on that team's roster that season (any week) or drafted by it.
    (season, None) -> everyone on any roster in the league that season."""
    pools = defaultdict(set)
    for season, code, pid in conn.execute(
            """
            select distinct season, franchise_code, player_id from core.player_week
            union
            select dp.season, dp.franchise_code, dp.player_id
            from core.draft_pick dp join core.draft d using (draft_id)
            where d.source = 'sleeper' and dp.player_id is not null and dp.franchise_code is not null
            """):
        if pid in players:
            pools[(season, code)].add(players[pid])
            pools[(season, None)].add(players[pid])
    return pools


class AllPlayers:
    """Every fantasy-relevant player, indexed by first name, last name and the start of the last name,
    so a typo in either part still finds the right person without comparing against all 12,000."""

    def __init__(self, players):
        self.index = defaultdict(set)
        for p in players.values():
            if not (p.positions & FANTASY_POSITIONS) or "DEF" in p.positions or not p.norm:
                continue
            for k in self._keys(p.norm):
                self.index[k].add(p.player_id)
        self.players = players

    @staticmethod
    def _keys(norm):
        tokens = canonical(norm).split()
        keys = {("first", tokens[0]), ("last", tokens[-1]), ("last3", tokens[-1][:3])}
        if len(tokens) > 1:
            keys.add(("first3", tokens[0][:3]))
        return keys

    def candidates(self, norm, season):
        ids = set()
        for k in self._keys(norm):
            ids |= self.index.get(k, set())
        return [self.players[i] for i in ids
                if self.players[i].rookie_year is None or self.players[i].rookie_year <= season]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def fits(player, allowed):
    return allowed is None or bool(player.positions & allowed)


def ranked(entry_norm, pool, allowed):
    """[(score, how, player)] best first. Ties go to the right position, then players seen in this league."""
    scored = []
    for p in pool:
        score, how = similarity(entry_norm, p.norm)
        scored.append((score, how, p))
    scored.sort(key=lambda s: (-s[0], not fits(s[2], allowed), not s[2].in_league, s[2].search_rank, s[2].player_id))
    return scored


def match_in_pool(entry, pool, allowed, label):
    """Best match inside a small pool (one team's roster, or everyone in the league that season)."""
    scored = ranked(normalize(entry.name), pool, allowed)
    if not scored or scored[0][0] < TEAM_MIN_SCORE:
        return None
    best_score, how, best = scored[0]
    runner_up = scored[1] if len(scored) > 1 else None
    if runner_up and best_score - runner_up[0] < TEAM_MIN_GAP:
        if best_score < GLOBAL_MIN_SCORE:
            return None                       # two players look alike; let the wider search decide
        return Match(best, f"{label}_{how}_tie", best_score, "check", scored[1:4],
                     "two players here have similar names")
    status = "auto" if best_score == 1.0 else "check"
    note = "" if fits(best, allowed) else "position differs from the workbook"
    return Match(best, f"{label}_{how}", best_score, status, scored[1:4], note)


def match_everywhere(entry, everyone, allowed):
    norm = normalize(entry.name)
    scored = ranked(norm, everyone.candidates(norm, entry.season), allowed)
    if not scored:
        return Match(None, "no_candidates", None, "unmatched")
    exact = [s for s in scored if s[0] == 1.0]
    if len(exact) == 1:
        p = exact[0][2]
        others = [s for s in scored if s[2] is not p][:3]
        if fits(p, allowed):
            return Match(p, "all_players_exact", 1.0, "auto", others)
        return Match(p, "all_players_exact", 1.0, "check", others, "position differs from the workbook")
    if exact:
        # Several players share this exact name. Only auto-pick when exactly one of them both played
        # in this league and fits the position typed; otherwise pick the likeliest and flag it.
        strong = [s for s in exact if s[2].in_league and fits(s[2], allowed)]
        if len(strong) == 1:
            p = strong[0][2]
            return Match(p, "all_players_exact_in_league", 1.0, "auto", [s for s in scored if s[2] is not p][:3],
                         f"{len(exact)} players share this name; picked the one who played in this league")
        exact.sort(key=lambda s: (not s[2].in_league, not fits(s[2], allowed), s[2].search_rank, s[2].player_id))
        p = exact[0][2]
        return Match(p, "all_players_exact_same_name", 1.0, "check", [s for s in scored if s[2] is not p][:3],
                     f"{len(exact)} players share this name; picked the most likely, please confirm")
    best_score, how, best = scored[0]
    if best_score >= GLOBAL_MIN_SCORE:
        close = len(scored) > 1 and best_score - scored[1][0] < GLOBAL_MIN_GAP
        return Match(best, f"all_players_{how}", best_score, "check", scored[1:4],
                     "another player's name is just as close" if close else "")
    return Match(None, "no_close_match", None, "unmatched", scored[:3])


def match_entry(entry, players, pools, everyone):
    allowed = entry.allowed

    if looks_like_defense(entry.name, entry.positions):
        abbr = defense_abbr(entry.name)
        if abbr and abbr in players:
            return Match(players[abbr], "defense", 1.0, "auto")
        if abbr:
            return Match(None, "defense", None, "unmatched",
                         note=f"Sleeper's player list has no defense '{abbr}'")
        return Match(None, "defense", None, "unmatched", note="couldn't tell which NFL team this is")

    seasons = [entry.season, entry.season - 1] if entry.source == "keeper" else [entry.season]
    team_pool = set().union(*(pools.get((s, entry.team_code), set()) for s in seasons)) if entry.team_code else set()
    league_pool = set().union(*(pools.get((s, None), set()) for s in seasons))

    if team_pool:                                          # 1. that team's roster (keepers: last season's too)
        found = match_in_pool(entry, team_pool, allowed, "team_roster")
        if found:
            return found
    if league_pool:                                        # 2. anyone in the league that season
        found = match_in_pool(entry, league_pool, allowed, "league_rosters")
        if found:
            if team_pool:                                  # listed under a team, but on someone else's roster
                found.status = "check"
                found.note = "; ".join(x for x in (found.note, "not on this team's roster") if x)
            return found

    found = match_everywhere(entry, everyone, allowed)     # 3. every player Sleeper knows
    if league_pool and found.player:                       # a Sleeper season, yet never on a league roster
        found.method += "_not_on_roster"
        found.status = "check"
        found.note = "; ".join(x for x in (found.note, "not on any roster in the league that season") if x)
    return found


# ---------------------------------------------------------------------------
# The review CSV
# ---------------------------------------------------------------------------

def read_csv():
    """Every row of the review file: key -> {status, player_id, note}. Handles Excel's own encoding."""
    if not CSV_PATH.exists():
        return {}, []
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with CSV_PATH.open(newline="", encoding=encoding) as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise LoadProblem("names", [f"Can't read {CSV_PATH.name}. Save it from Excel as 'CSV UTF-8'."])

    found, warnings = {}, []
    for line, row in enumerate(rows, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        status = row.get("status", "").lower().replace(" ", "_")
        if status not in STATUS_ORDER:
            if status:
                warnings.append(f"line {line}: status '{row.get('status')}' isn't one I know; re-matching that row")
            continue
        try:
            season = int(float(row.get("season", "")))
        except ValueError:
            warnings.append(f"line {line}: season '{row.get('season')}' isn't a year; ignoring that row")
            continue
        player_id = row.get("player_id", "")
        if re.fullmatch(r"\d+\.0", player_id):            # Excel turned 4866 into 4866.0
            player_id = player_id[:-2]
        key = (row.get("source", "").lower(), season, row.get("team", "").upper() or None,
               clean_name(row.get("name_as_entered")))
        found[key] = {"status": status, "player_id": player_id or None, "method": row.get("method", ""),
                      "note": row.get("note", ""), "your_notes": row.get("your_notes", "")}
    return found, warnings


def your_decision(row, last_written_id):
    """The row as you left it, if you made a decision in it; otherwise None (re-match it).
    Typing a player_id counts as a decision even if you forgot to change the status."""
    if row is None:
        return None
    if row["status"] in REVIEWED:
        return row
    if row["player_id"] and (row["status"] == "unmatched" or row["player_id"] != last_written_id):
        return {**row, "status": "confirmed", "note": row["note"] or "player_id typed in by hand"}
    if row["status"] == "unmatched" and row["method"] == "reviewed_bad_id":
        return {**row, "status": "confirmed", "player_id": None}     # still waiting on a good id from you
    return None


def candidates_text(candidates):
    return "; ".join(f"{p.name} [{'/'.join(sorted(p.positions & FANTASY_POSITIONS)) or '?'}] "
                     f"id {p.player_id} ({score:.2f})" for score, _, p in candidates)


def write_csv(rows):
    rows = sorted(rows, key=lambda r: (STATUS_ORDER[r["status"]], r["source"], r["season"],
                                       r["team"] or "", r["name_as_entered"]))
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError:
        raise LoadProblem("names", [f"Can't save {CSV_PATH.name}. Close it in Excel, then re-run."])


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(conn):
    entries = collect_entries(conn)
    players = fetch_players(conn)
    if not players:
        raise LoadProblem("names", ["core.player is empty. Run the 'players' step first: python -m etl.load_core"])
    pools = team_pools(conn, players)
    everyone = AllPlayers(players)
    in_csv, warnings = read_csv()
    last_written = {(s, y, t, n): pid for s, y, t, n, pid in conn.execute(
        "select source, season, team_code, name_as_entered, player_id from core.player_name_map")}

    rows = []
    dropped = [k for k, r in in_csv.items() if k not in entries and r["status"] in REVIEWED]
    for key, entry in entries.items():
        mine = your_decision(in_csv.get(key), last_written.get(key, in_csv.get(key, {}).get("player_id")))
        if mine:
            player = players.get(mine["player_id"]) if mine["player_id"] else None
            note = mine["note"] if mine["note"] == "player_id typed in by hand" else ""
            match = Match(player, "reviewed", None, mine["status"], note=note)
            if mine["status"] == "not_a_player":
                match.player = None
            elif not player:                       # confirmed, but the id is blank or not a Sleeper player
                match = match_entry(entry, players, pools, everyone)
                match.candidates = ([(match.score or 0, "", match.player)] if match.player else []) + match.candidates
                match.player, match.score, match.status, match.method = None, None, "unmatched", "reviewed_bad_id"
                match.note = (f"player_id '{mine['player_id']}' isn't in Sleeper's player list" if mine["player_id"]
                              else mine["note"] if mine["method"] == "reviewed_bad_id" and mine["note"]
                              else "marked confirmed but player_id is blank")
        else:
            match = match_entry(entry, players, pools, everyone)
        p = match.player
        rows.append({
            "status": match.status, "source": entry.source, "season": entry.season, "team": entry.team_code or "",
            "name_as_entered": entry.name, "position": entry.position, "player_id": p.player_id if p else "",
            "matched_name": p.name if p else "",
            "matched_position": "/".join(sorted(p.positions & FANTASY_POSITIONS)) if p else "",
            "method": match.method, "score": f"{match.score:.3f}" if match.score is not None else "",
            "candidates": candidates_text(match.candidates[:3]) if match.status in ("check", "unmatched") else "",
            "note": match.note,
            "your_notes": in_csv.get(key, {}).get("your_notes", ""),
        })

    write_csv(rows)
    conn.execute("delete from core.player_name_map")
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into core.player_name_map (source, season, team_code, name_as_entered, position, player_id,
                                              matched_name, method, score, status, candidates)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(r["source"], r["season"], r["team"] or None, r["name_as_entered"], r["position"] or None,
              r["player_id"] or None, r["matched_name"] or None, r["method"], r["score"] or None,
              r["status"], r["candidates"] or None) for r in rows],
        )

    counts = defaultdict(int)
    for r in rows:
        counts[r["status"]] += 1
    print(f"    {len(rows)} workbook names: " + ", ".join(f"{counts[s]} {s}" for s in STATUS_ORDER if counts[s]))
    print(f"    review file: data/manual/{CSV_PATH.name}")
    for w in warnings:
        print(f"    note: {w}")
    if dropped:
        print(f"    note: {len(dropped)} reviewed row(s) are no longer in the workbook and were dropped")
    return counts


def main():
    with get_connection() as conn:
        try:
            run(conn)
            conn.commit()
        except LoadProblem as problem:
            conn.rollback()
            print("\nStopped. Nothing was saved. Problems:")
            for line in problem.problems[:40]:
                print(f"  - {line}")
            sys.exit(1)
        except psycopg.Error as error:
            conn.rollback()
            print(f"\nStopped. Nothing was saved. The database said:\n  {error}")
            sys.exit(1)


if __name__ == "__main__":
    main()
