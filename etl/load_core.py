"""
etl/load_core.py

Phase 2, step 2: build the clean (core) tables from the raw layer.
Reads raw.v_sleeper_latest (Sleeper) and raw.workbook_row (All-In-One workbook).

Steps, in order (each one is its own transaction, so a failure leaves earlier steps saved):
  seasons       season settings, scoring rules, lineup slots           (Sleeper)
  owners        owners, plus each franchise's owner / team name /
                division / Sleeper season totals per season              (Sleeper)
  players       every player, plus placeholder rows for ids that
                Sleeper's player list doesn't have                       (Sleeper)
  weeks         team_week + player_week: scores, lineups, every
                rostered player's points                                 (Sleeper)
  games         core.game, the OFFICIAL record: workbook games, the
                season score policy, and the seed overrides              (workbook + Sleeper)
  playoffs      Sleeper's brackets (reference only)                      (Sleeper)
  drafts        drafts and picks, 2021 on, and whose slot each draft
                column was (for traded picks)                            (Sleeper)
  transactions  trades, waivers, adds/drops, traded picks, traded FAAB   (Sleeper)
  rankings      weekly top-4 power rankings                              (workbook)
  awards        individual and team awards                               (workbook)
  names         match workbook player names to Sleeper ids; writes
                data/manual/player_name_map.csv for review              (workbook + Sleeper)
  keepers       core.keeper from the Keepers sheet                       (workbook)
  draft_2020    the 2020 ESPN draft from the Draft sheet                 (workbook)
  award_players link individual awards to player ids                     (workbook)

Safe to re-run: every step replaces what it owns. Names you confirmed in the CSV are kept.

Run from the project root:
    python -m etl.load_core                    # everything
    python -m etl.load_core --step games       # just one step (repeatable)
    python -m etl.load_core --from names       # after editing player_name_map.csv
Then check:  select * from qa.summary;
"""

import argparse
import re
import sys
from collections import defaultdict
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from etl import match_players
from etl.common import (
    LoadProblem, clean_name, franchise_code_for, from_millis, is_empty_slot,
    latest, roster_map, season_points, to_bool, to_number, winner_of, workbook_rows,
)
from etl.db import get_connection
from etl.precompute import data_changed, data_changing


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def load_seasons(conn):
    leagues = latest(conn, "league")
    if not leagues:
        raise LoadProblem("seasons", ["No Sleeper league data in raw. Run: python -m etl.pull_sleeper"])

    with conn.cursor() as cur:
        for season, _, league in leagues:
            settings = league.get("settings") or {}
            previous = league.get("previous_league_id")
            conn.execute(
                "insert into core.season (season, platform, sleeper_league_id) values (%s, 'sleeper', %s) "
                "on conflict (season) do nothing",
                (season, league["league_id"]),
            )
            conn.execute(
                """
                update core.season set
                    sleeper_league_id = %s, sleeper_previous_league_id = %s, sleeper_draft_id = %s,
                    status = %s, num_teams = %s, playoff_week_start = %s, playoff_teams = %s,
                    last_scored_week = %s, league_settings = %s, updated_at = now()
                where season = %s
                """,
                (
                    league["league_id"], None if previous in (None, "0") else previous, league.get("draft_id"),
                    league.get("status"), league.get("total_rosters"), settings.get("playoff_week_start"),
                    settings.get("playoff_teams"), settings.get("last_scored_leg"),
                    Jsonb(settings), season,
                ),
            )

            conn.execute("delete from core.scoring_setting where season = %s", (season,))
            cur.executemany(
                "insert into core.scoring_setting (season, stat_key, points) values (%s, %s, %s)",
                [(season, key, value) for key, value in (league.get("scoring_settings") or {}).items()],
            )

            positions = league.get("roster_positions") or []
            cur.executemany(
                "insert into core.roster_slot (season, slot_index, slot) values (%s, %s, %s) "
                "on conflict (season, slot_index) do update set slot = excluded.slot",
                [(season, i, slot) for i, slot in enumerate(positions)],
            )
            conn.execute("delete from core.roster_slot where season = %s and slot_index >= %s",
                         (season, len(positions)))
            print(f"    {season}: {len(positions)} lineup slots, "
                  f"{len(league.get('scoring_settings') or {})} scoring rules")


def _ensure_franchise_rows(conn, season):
    """A season the seed file doesn't know yet (e.g. 2027) inherits last season's roster -> franchise map."""
    has_rows = conn.execute(
        "select exists (select 1 from core.franchise_season where season = %s and sleeper_roster_id is not null)",
        (season,),
    ).fetchone()[0]
    if has_rows:
        return
    copied = conn.execute(
        """
        insert into core.franchise_season (season, franchise_code, sleeper_roster_id)
        select %s, franchise_code, sleeper_roster_id
        from core.franchise_season
        where sleeper_roster_id is not null
          and season = (select max(season) from core.franchise_season
                        where season < %s and sleeper_roster_id is not null)
        on conflict do nothing
        """,
        (season, season),
    ).rowcount
    print(f"    {season}: not in the seed file, copied {copied} roster slots from the season before")


def load_owners(conn):
    users_by_season = {season: payload or [] for season, _, payload in latest(conn, "users")}
    rosters_by_season = {season: payload or [] for season, _, payload in latest(conn, "rosters")}
    leagues = {season: payload for season, _, payload in latest(conn, "league")}

    # Owners: oldest season first, so the newest display name wins.
    with conn.cursor() as cur:
        cur.executemany(
            "insert into core.owner (sleeper_user_id, display_name) values (%s, %s) "
            "on conflict (sleeper_user_id) do update set display_name = excluded.display_name",
            [(u["user_id"], u.get("display_name"))
             for season in sorted(users_by_season) for u in users_by_season[season]],
        )
    owner_ids = dict(conn.execute(
        "select sleeper_user_id, owner_id from core.owner where sleeper_user_id is not null").fetchall())

    problems = []
    for season in sorted(rosters_by_season):
        _ensure_franchise_rows(conn, season)
        users = {u["user_id"]: u for u in users_by_season.get(season, [])}
        meta = (leagues.get(season) or {}).get("metadata") or {}
        for roster in rosters_by_season[season]:
            st = roster.get("settings") or {}
            user = users.get(roster.get("owner_id")) or {}
            team_name = ((user.get("metadata") or {}).get("team_name") or user.get("display_name") or "").strip()
            division = st.get("division") or None
            division_name = meta.get(f"division_{division}") if division else None
            first_word = division_name.split()[0].upper() if division_name else None
            updated = conn.execute(
                """
                update core.franchise_season set
                    owner_id = %s, team_name = %s, division_number = %s, division_name = %s, conference = %s,
                    reported_wins = %s, reported_losses = %s, reported_ties = %s,
                    reported_points_for = %s, reported_points_against = %s, reported_max_points = %s
                where season = %s and sleeper_roster_id = %s
                """,
                (
                    owner_ids.get(roster.get("owner_id")), team_name or None, division, division_name,
                    first_word if first_word in ("AFC", "NFC") else None,
                    st.get("wins"), st.get("losses"), st.get("ties"),
                    season_points(st, "fpts"), season_points(st, "fpts_against"), season_points(st, "ppts"),
                    season, roster["roster_id"],
                ),
            ).rowcount
            if not updated:
                problems.append(f"{season}: Sleeper roster {roster['roster_id']} isn't mapped to a franchise "
                                f"in the seed file (db/seed/001_reference_data.sql)")
        print(f"    {season}: {len(rosters_by_season[season])} teams")
    if problems:
        raise LoadProblem("owners", problems)


def load_players(conn):
    row = conn.execute("select payload from raw.v_sleeper_latest where endpoint = 'players'").fetchone()
    players = (row[0] if row else None) or {}
    if not players:
        print("    note: no /players/nfl in raw yet; only placeholder rows will be made")

    def as_text(value):  # ids come as numbers or text, sometimes with stray spaces (" 00-0035659")
        text = None if value is None else str(value).strip()
        return text or None

    def birth(value):
        try:
            return date.fromisoformat(value) if value else None
        except ValueError:
            return None

    rows = []
    for pid, p in players.items():
        first, last = p.get("first_name"), p.get("last_name")
        full = p.get("full_name") or " ".join(x for x in (first, last) if x) or None
        rookie = (p.get("metadata") or {}).get("rookie_year")
        rows.append((
            pid, full, first, last, p.get("position"), p.get("fantasy_positions"), p.get("team"),
            p.get("status"), birth(p.get("birth_date")),
            int(rookie) if rookie and str(rookie).isdigit() else None,
            p.get("college"), as_text(p.get("gsis_id")), as_text(p.get("espn_id")),
            as_text(p.get("yahoo_id")), as_text(p.get("sportradar_id")),
        ))
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into core.player (player_id, full_name, first_name, last_name, position, fantasy_positions,
                                     nfl_team, status, birth_date, rookie_year, college, gsis_id, espn_id,
                                     yahoo_id, sportradar_id, is_stub, updated_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, now())
            on conflict (player_id) do update set
                full_name = excluded.full_name, first_name = excluded.first_name, last_name = excluded.last_name,
                position = excluded.position, fantasy_positions = excluded.fantasy_positions,
                nfl_team = excluded.nfl_team, status = excluded.status, birth_date = excluded.birth_date,
                rookie_year = excluded.rookie_year, college = excluded.college,
                gsis_id = coalesce(excluded.gsis_id, core.player.gsis_id),
                espn_id = excluded.espn_id, yahoo_id = excluded.yahoo_id, sportradar_id = excluded.sportradar_id,
                is_stub = false, updated_at = now()
            """,
            rows,
        )

    # Placeholder rows for every player id the league data mentions but /players/nfl doesn't have.
    referenced = {}  # player_id -> (name, position) if we know it
    for _, _, payload in latest(conn, "matchups"):
        for m in payload or []:
            for pid in list(m.get("players") or []) + list(m.get("starters") or []) + list(m.get("players_points") or {}):
                if not is_empty_slot(pid):
                    referenced.setdefault(pid, (None, None))
    for _, _, payload in latest(conn, "draft_picks"):
        for pick in payload or []:
            if pick.get("player_id"):
                meta = pick.get("metadata") or {}
                name = " ".join(x for x in (meta.get("first_name"), meta.get("last_name")) if x) or None
                referenced[pick["player_id"]] = (name, meta.get("position"))
    for _, _, payload in latest(conn, "transactions"):
        for t in payload or []:
            for pid in list((t.get("adds") or {})) + list((t.get("drops") or {})):
                referenced.setdefault(pid, (None, None))

    missing = [pid for pid in referenced if pid not in players]
    with conn.cursor() as cur:
        cur.executemany(
            "insert into core.player (player_id, full_name, position, is_stub) values (%s, %s, %s, true) "
            "on conflict (player_id) do nothing",
            [(pid, *referenced[pid]) for pid in missing],
        )
    print(f"    {len(rows):,} players from Sleeper's list, {len(missing)} placeholder(s) for unknown ids")


def load_weeks(conn):
    rosters = roster_map(conn)
    slots = {(season, idx): slot for season, idx, slot in
             conn.execute("select season, slot_index, slot from core.roster_slot")}
    by_season = defaultdict(list)
    for season, week, payload in latest(conn, "matchups"):
        by_season[season].append((week, payload or []))

    problems = []
    for season in sorted(by_season):
        team_rows, player_rows = [], []
        for week, payload in by_season[season]:
            for m in payload:
                code = rosters.get((season, m.get("roster_id")))
                if not code:
                    problems.append(f"{season} week {week}: roster {m.get('roster_id')} has no franchise")
                    continue
                starters = m.get("starters") or []
                starter_points = m.get("starters_points") or []
                points_by_player = m.get("players_points") or {}
                team_rows.append((
                    season, week, code, m.get("roster_id"), m.get("matchup_id"),
                    m.get("points") or 0, m.get("custom_points"),
                    sum(1 for pid in starters if is_empty_slot(pid)),
                ))
                starter_index = {pid: i for i, pid in enumerate(starters) if not is_empty_slot(pid)}
                everyone = set(m.get("players") or []) | set(points_by_player) | set(starter_index)
                for pid in everyone:
                    idx = starter_index.get(pid)
                    pts = points_by_player.get(pid)
                    if pts is None and idx is not None and idx < len(starter_points):
                        pts = starter_points[idx]
                    slot = "BN" if idx is None else slots.get((season, idx))
                    if slot is None:
                        problems.append(f"{season} week {week}: {code} starter in slot #{idx}, "
                                        f"but the season only has {sum(1 for s, _ in slots if s == season)} slots")
                        continue
                    player_rows.append((season, week, code, pid, pts or 0, idx is not None, idx, slot))
        if problems:
            break
        conn.execute("delete from core.player_week where season = %s", (season,))
        with conn.cursor() as cur:
            cur.executemany(
                """
                insert into core.team_week (season, week, franchise_code, sleeper_roster_id, matchup_id,
                                            points, custom_points, empty_starter_slots, source)
                values (%s, %s, %s, %s, %s, %s, %s, %s, 'sleeper')
                on conflict (season, week, franchise_code) do update set
                    sleeper_roster_id = excluded.sleeper_roster_id, matchup_id = excluded.matchup_id,
                    points = excluded.points, custom_points = excluded.custom_points,
                    empty_starter_slots = excluded.empty_starter_slots
                """,
                team_rows,
            )
            cur.executemany(
                """
                insert into core.player_week (season, week, franchise_code, player_id, points,
                                              is_starter, slot_index, lineup_slot)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                player_rows,
            )
        weeks = len(by_season[season])
        print(f"    {season}: {weeks} weeks, {len(team_rows)} team scores, {len(player_rows):,} player scores")
    if problems:
        raise LoadProblem("weeks", problems)


GAME_TYPES = {"regular season": "regular", "playoff": "playoff", "consolation": "consolation"}


def load_games(conn):
    rows = workbook_rows(conn, "Scores")
    policy = dict(conn.execute("select season, official_scores_from from core.season").fetchall())
    teams = {(s, c) for s, c in conn.execute("select season, franchise_code from core.franchise_season")}
    sleeper = {(s, w, c): float(p) for s, w, c, p in
               conn.execute("select season, week, franchise_code, points from core.team_week")}
    overrides = {gid: (winner, known, reason) for gid, winner, known, reason in
                 conn.execute("select game_id, winner_code, scores_known, reason from core.game_override")}

    games, problems = [], []
    counts = defaultdict(int)
    for row_number, d in rows:
        where = f"Scores row {row_number} ({d.get('Game ID')})"
        try:
            season, week = int(d["Year"]), int(d["Week"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"{where}: Year/Week missing or not a number")
            continue
        a, b = franchise_code_for(d.get("Team 1"), season), franchise_code_for(d.get("Team 2"), season)
        game_type = GAME_TYPES.get(str(d.get("Game Type") or "").strip().lower())
        for label, code, name in (("Team 1", a, d.get("Team 1")), ("Team 2", b, d.get("Team 2"))):
            if not code:
                problems.append(f"{where}: don't recognize {label} '{name}'")
            elif (season, code) not in teams:
                problems.append(f"{where}: {code} has no {season} season in the seed file")
        if not game_type:
            problems.append(f"{where}: unknown Game Type '{d.get('Game Type')}'")
        if not (a and b and game_type) or (season, a) not in teams or (season, b) not in teams:
            continue

        score_a, score_b = to_number(d.get("Team 1 Score")), to_number(d.get("Team 2 Score"))
        (t1, p1), (t2, p2) = sorted([(a, score_a), (b, score_b)])
        game_id = f"{season}-{week}-{t1}-{t2}"
        winner = winner_of(t1, t2, p1, p2)
        source = "sheet"

        # Sleeper-scored seasons: use Sleeper's numbers when they agree on the winner.
        s1, s2 = sleeper.get((season, week, t1)), sleeper.get((season, week, t2))
        if policy.get(season) == "sleeper" and s1 is not None and s2 is not None \
                and winner_of(t1, t2, s1, s2) == winner:
            p1, p2, source = s1, s2, "sleeper"

        note = None
        if game_id in overrides:
            override_winner, scores_known, note = overrides[game_id]
            winner = override_winner
            if not scores_known:
                p1 = p2 = None
            counts["overridden"] += 1
        counts[source] += 1

        games.append((
            game_id, season, week, game_type, d.get("Special Designation"), t1, t2, p1, p2, winner, source, note,
            d.get("Inter. Location"), to_bool(d.get("Game of the Week")),
            to_bool(d.get("Division")), to_bool(d.get("Conference")),
            False,                                                   # is_provisional
        ))

    if problems:
        raise LoadProblem("games", problems)

    # Provisional games (db/schema/011): a regular-season week Sleeper has finished scoring but the workbook
    # doesn't have yet, in seasons whose official scores come from Sleeper. Team by team: if the workbook
    # already has a team's game that week (e.g. a half-entered week), the workbook's game wins.
    # When the workbook catches up, its row has the same game_id and replaces the provisional one.
    has_game = {(g[1], g[2], code) for g in games for code in (g[5], g[6])}
    pairs = conn.execute(
        """
        select tw.season, tw.week, min(tw.franchise_code), max(tw.franchise_code)
        from core.team_week tw
        join core.season s on s.season = tw.season
        where s.official_scores_from = 'sleeper'
          and tw.matchup_id is not null
          and tw.week < s.playoff_week_start            -- regular season only; playoffs wait for the workbook
          and tw.week <= s.last_scored_week             -- weeks Sleeper has marked as scored
        group by tw.season, tw.week, tw.matchup_id
        having count(*) = 2
        order by 1, 2, 3
        """
    ).fetchall()
    for season, week, t1, t2 in pairs:
        if (season, week, t1) in has_game or (season, week, t2) in has_game:
            continue
        game_id = f"{season}-{week}-{t1}-{t2}"
        p1, p2 = sleeper[(season, week, t1)], sleeper[(season, week, t2)]
        winner = winner_of(t1, t2, p1, p2)
        note = "Provisional: from Sleeper; the All-In-One workbook doesn't have this week yet"
        if game_id in overrides:
            override_winner, scores_known, override_note = overrides[game_id]
            winner, note = override_winner, f"{note}. {override_note}"
            if not scores_known:
                p1 = p2 = None
            counts["overridden"] += 1
        counts["provisional"] += 1
        games.append((game_id, season, week, "regular", None, t1, t2, p1, p2, winner, "sleeper", note,
                      None, False, False, False, True))

    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into core.game (game_id, season, week, game_type, designation, team1_code, team2_code,
                                   team1_points, team2_points, winner_code, points_source, result_note,
                                   international_location, is_game_of_week, is_division_game, is_conference_game,
                                   is_provisional)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (game_id) do update set
                season = excluded.season, week = excluded.week, game_type = excluded.game_type,
                designation = excluded.designation, team1_points = excluded.team1_points,
                team2_points = excluded.team2_points, winner_code = excluded.winner_code,
                points_source = excluded.points_source, result_note = excluded.result_note,
                international_location = excluded.international_location,
                is_game_of_week = excluded.is_game_of_week, is_division_game = excluded.is_division_game,
                is_conference_game = excluded.is_conference_game, is_provisional = excluded.is_provisional
            """,
            games,
        )
    removed = conn.execute("delete from core.game where not (game_id = any(%s))",
                           ([g[0] for g in games],)).rowcount
    print(f"    {len(games)} games: {counts['sleeper']} with Sleeper scores, {counts['sheet']} with workbook scores, "
          f"{counts['overridden']} with a seed override, {counts['provisional']} provisional from Sleeper"
          + (f", {removed} old game(s) removed" if removed else ""))


def _bracket_from(source):
    if not source:
        return None
    if "w" in source:
        return f"W{source['w']}"
    if "l" in source:
        return f"L{source['l']}"
    return None


def load_playoffs(conn):
    rosters = roster_map(conn)
    week_start = dict(conn.execute("select season, playoff_week_start from core.season").fetchall())
    total = 0
    for bracket, endpoint in (("winners", "winners_bracket"), ("losers", "losers_bracket")):
        for season, _, payload in latest(conn, endpoint):
            conn.execute("delete from core.playoff_game where season = %s and bracket = %s", (season, bracket))
            code = lambda rid: rosters.get((season, rid)) if rid is not None else None
            rows = [(
                season, bracket, g["m"], g["r"],
                (week_start.get(season) or 15) + g["r"] - 1,
                code(g.get("t1")), code(g.get("t2")),
                _bracket_from(g.get("t1_from")), _bracket_from(g.get("t2_from")),
                code(g.get("t1_original")), code(g.get("t2_original")),
                code(g.get("w")), code(g.get("l")), g.get("p"),
            ) for g in payload or []]
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    insert into core.playoff_game (season, bracket, match_id, round, week, team1_code, team2_code,
                        team1_from, team2_from, team1_original_code, team2_original_code,
                        winner_code, loser_code, place_decided)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            total += len(rows)
    print(f"    {total} bracket games")


def load_drafts(conn):
    rosters = roster_map(conn)
    # (season, Sleeper user_id) -> franchise, for Sleeper's draft_order (user_id -> draft slot)
    by_user = {
        (season, uid): code
        for season, uid, code in conn.execute(
            "select fs.season, o.sleeper_user_id, fs.franchise_code from core.franchise_season fs "
            "join core.owner o on o.owner_id = fs.owner_id where o.sleeper_user_id is not null"
        )
    }
    drafts, slot_rows = 0, []
    for season, _, d in latest(conn, "draft"):
        if not d:
            continue
        draft_season = int(d.get("season") or season)
        conn.execute(
            """
            insert into core.draft (draft_id, season, draft_type, rounds, status, started_at, source)
            values (%s, %s, %s, %s, %s, %s, 'sleeper')
            on conflict (draft_id) do update set
                season = excluded.season, draft_type = excluded.draft_type, rounds = excluded.rounds,
                status = excluded.status, started_at = excluded.started_at
            """,
            (d["draft_id"], draft_season, d.get("type"),
             (d.get("settings") or {}).get("rounds"), d.get("status"), from_millis(d.get("start_time"))),
        )
        drafts += 1
        # Phase 9: whose slot each draft column originally was (a traded pick is made in the original team's slot).
        # Sleeper's slot_to_roster_id when the draft has it, else draft_order (user_id -> slot).
        by_slot = {}
        for slot, rid in (d.get("slot_to_roster_id") or {}).items():
            if rid is not None and rosters.get((draft_season, int(rid))):
                by_slot[int(slot)] = (rosters[(draft_season, int(rid))], "slot_to_roster_id")
        if not by_slot:
            for uid, slot in (d.get("draft_order") or {}).items():
                if slot is not None and by_user.get((draft_season, str(uid))):
                    by_slot[int(slot)] = (by_user[(draft_season, str(uid))], "draft_order")
        slot_rows += [(d["draft_id"], slot, code, source) for slot, (code, source) in by_slot.items()]

    conn.execute("delete from core.draft_slot where draft_id in (select draft_id from core.draft where source = 'sleeper')")
    with conn.cursor() as cur:
        cur.executemany("insert into core.draft_slot (draft_id, draft_slot, franchise_code, source) "
                        "values (%s, %s, %s, %s) on conflict do nothing", slot_rows)

    problems, total = [], 0
    for season, _, picks in latest(conn, "draft_picks"):
        by_draft = defaultdict(list)
        for p in picks or []:
            code = rosters.get((season, p.get("roster_id")))
            if not code:
                problems.append(f"{season} draft pick {p.get('pick_no')}: roster {p.get('roster_id')} has no franchise")
                continue
            by_draft[p["draft_id"]].append((
                p["draft_id"], p["pick_no"], season, p["round"], p.get("draft_slot"), code,
                p.get("player_id"), bool(p.get("is_keeper")),
            ))
        for draft_id, rows in by_draft.items():
            conn.execute("delete from core.draft_pick where draft_id = %s", (draft_id,))
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    insert into core.draft_pick (draft_id, pick_no, season, round, draft_slot,
                                                 franchise_code, player_id, is_keeper)
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            total += len(rows)
    if problems:
        raise LoadProblem("drafts", problems)
    print(f"    {drafts} drafts, {total:,} picks, {len(slot_rows)} draft slots with their original team")


def load_transactions(conn):
    rosters = roster_map(conn)
    owner_ids = dict(conn.execute(
        "select sleeper_user_id, owner_id from core.owner where sleeper_user_id is not null").fetchall())
    by_season = defaultdict(dict)
    for season, week, payload in latest(conn, "transactions"):
        for t in payload or []:
            by_season[season][t["transaction_id"]] = (week, t)

    problems = []
    for season in sorted(by_season):
        code = lambda rid: rosters.get((season, rid))
        parents, players, picks, faab = [], [], [], defaultdict(int)
        for tid, (week, t) in by_season[season].items():
            parents.append((
                tid, season, t.get("leg") or week, t.get("type"), t.get("status"),
                from_millis(t.get("created")), from_millis(t.get("status_updated")),
                owner_ids.get(t.get("creator")), (t.get("settings") or {}).get("waiver_bid"),
                (t.get("metadata") or {}).get("notes"),
            ))
            for action, moves in (("add", t.get("adds")), ("drop", t.get("drops"))):
                for pid, rid in (moves or {}).items():
                    if not code(rid):
                        problems.append(f"{season} transaction {tid}: roster {rid} has no franchise")
                        continue
                    players.append((tid, pid, action, code(rid)))
            for pk in t.get("draft_picks") or []:
                original, sender, receiver = code(pk.get("roster_id")), code(pk.get("previous_owner_id")), code(pk.get("owner_id"))
                if not (original and sender and receiver):
                    problems.append(f"{season} transaction {tid}: traded pick has an unknown roster")
                    continue
                picks.append((tid, int(pk["season"]), pk["round"], original, sender, receiver))
            for money in t.get("waiver_budget") or []:
                sender, receiver = code(money.get("sender")), code(money.get("receiver"))
                if not (sender and receiver):
                    problems.append(f"{season} transaction {tid}: FAAB with an unknown roster")
                    continue
                faab[(tid, sender, receiver)] += money.get("amount") or 0
        if problems:
            break

        conn.execute("delete from core.transaction where season = %s", (season,))  # children go with it
        with conn.cursor() as cur:
            cur.executemany(
                """
                insert into core.transaction (transaction_id, season, week, type, status, created_at,
                                              status_updated_at, creator_owner_id, waiver_bid, notes)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                parents,
            )
            cur.executemany(
                "insert into core.transaction_player (transaction_id, player_id, action, franchise_code) "
                "values (%s, %s, %s, %s) on conflict do nothing",
                players,
            )
            cur.executemany(
                "insert into core.transaction_pick (transaction_id, pick_season, pick_round, original_code, "
                "from_code, to_code) values (%s, %s, %s, %s, %s, %s) on conflict do nothing",
                picks,
            )
            cur.executemany(
                "insert into core.transaction_faab (transaction_id, from_code, to_code, amount) "
                "values (%s, %s, %s, %s)",
                [(tid, s, r, amount) for (tid, s, r), amount in faab.items()],
            )
        print(f"    {season}: {len(parents)} transactions, {len(players)} player moves, "
              f"{len(picks)} traded picks, {len(faab)} FAAB transfers")
    if problems:
        raise LoadProblem("transactions", problems)


def load_rankings(conn):
    rows = workbook_rows(conn, "Rankings")
    teams = {(s, c) for s, c in conn.execute("select season, franchise_code from core.franchise_season")}
    out, problems = [], []
    for row_number, d in rows:
        season = int(d["Year"])
        week_match = re.match(r"\s*(\d+)", str(d.get("Week") or ""))
        if not week_match:
            problems.append(f"Rankings row {row_number}: can't read week '{d.get('Week')}'")
            continue
        week = int(week_match.group(1))          # "1 (Pre Season)" -> 1
        for rank in range(1, 5):
            name = d.get(f"Rank {rank}")
            if not name:
                continue
            code = franchise_code_for(name, season)
            if not code or (season, code) not in teams:
                problems.append(f"Rankings row {row_number}: don't recognize '{name}' for {season}")
                continue
            out.append((season, week, rank, code))
    if problems:
        raise LoadProblem("rankings", problems)
    conn.execute("delete from core.power_ranking")
    with conn.cursor() as cur:
        cur.executemany(
            "insert into core.power_ranking (season, week, rank, franchise_code) values (%s, %s, %s, %s)",
            out,
        )
    print(f"    {len(out)} ranking spots across {len({(s, w) for s, w, _, _ in out})} weeks")


AWARD_NAMES = {"bozo of the year": "Bozo of the Year"}   # fixes inconsistent capitalization


def load_awards(conn):
    individual = workbook_rows(conn, "Individual Awards")
    team = workbook_rows(conn, "Team Awards")
    rows, problems = [], []

    for row_number, d in individual:
        season = int(d["Year"])
        award = AWARD_NAMES.get(str(d["Award"]).strip().lower(), str(d["Award"]).strip())
        team_name = str(d.get("Team") or "").strip()
        conference = team_name.upper() if team_name.upper() in ("AFC", "NFC") else None
        code = None if conference or not team_name else franchise_code_for(team_name, season)
        if team_name and not conference and not code:
            problems.append(f"Individual Awards row {row_number}: don't recognize team '{team_name}'")
            continue
        rows.append((season, award, code, d.get("Name"), d.get("Position"), conference, None, False))

    for row_number, d in team:
        season = int(d["Year"])
        award = AWARD_NAMES.get(str(d["Award"]).strip().lower(), str(d["Award"]).strip())
        code = franchise_code_for(d.get("Team"), season)
        if not code:
            problems.append(f"Team Awards row {row_number}: don't recognize team '{d.get('Team')}'")
            continue
        rows.append((season, award, code, None, None, d.get("Conference"), d.get("Division"),
                     to_bool(d.get("Traveling Trophy"))))

    if problems:
        raise LoadProblem("awards", problems)
    conn.execute("delete from core.award")
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into core.award (season, award_name, franchise_code, player_name_as_entered, position,
                                    conference, award_group, is_traveling_trophy)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            """,
            rows,
        )
    saved = conn.execute("select count(*) from core.award").fetchone()[0]
    skipped = len(rows) - saved
    print(f"    {saved} awards" + (f" ({skipped} exact duplicate row(s) in the workbook skipped)" if skipped else ""))


# ---------------------------------------------------------------------------
# Workbook player names -> Sleeper ids (keepers, 2020 draft, award winners)
# ---------------------------------------------------------------------------

def load_names(conn):
    match_players.run(conn)


def name_map(conn, source):
    """(season, team_code, name_as_entered) -> player_id, for matches we trust enough to use."""
    return {
        (season, team, name): pid
        for season, team, name, pid in conn.execute(
            "select season, team_code, name_as_entered, player_id from core.player_name_map "
            "where source = %s and player_id is not null and status in ('auto', 'check', 'confirmed')",
            (source,),
        )
    }


def load_keepers(conn):
    ids = name_map(conn, "keeper")
    rows, waiting, problems = [], [], []
    for row_number, d in workbook_rows(conn, "Keepers"):
        season, name = int(d["Year"]), clean_name(d.get("Name"))
        code = franchise_code_for(d.get("Team"), season)
        if not code:
            problems.append(f"Keepers row {row_number}: don't recognize team '{d.get('Team')}'")
            continue
        if not name:
            continue
        pid = ids.get((season, code, name))
        if pid:
            rows.append((season, code, pid, name))
        else:
            waiting.append(f"{season} {code} {name}")
    if problems:
        raise LoadProblem("keepers", problems)

    conn.execute("delete from core.keeper")
    with conn.cursor() as cur:
        cur.executemany(
            "insert into core.keeper (season, franchise_code, player_id, name_as_entered) "
            "values (%s, %s, %s, %s) on conflict do nothing",
            rows,
        )
    saved = conn.execute("select count(*) from core.keeper").fetchone()[0]
    print(f"    {saved} keepers")
    if waiting:
        print(f"    {len(waiting)} keeper(s) waiting on a player id (see qa.unmatched_names): " + ", ".join(waiting[:5])
              + (" ..." if len(waiting) > 5 else ""))
    if saved < len(rows):
        print(f"    note: {len(rows) - saved} keeper row(s) point at a player already kept by that team that year")


def load_sheet_drafts(conn):
    """Drafts from seasons Sleeper doesn't have (2020, ESPN), from the workbook's Draft sheet.
    The sheet has the round and team for each pick but not the pick number, so pick_no is the
    sheet's row order (flagged pick_no_is_estimated). 43 picks have no team ('?')."""
    seasons = [s for (s,) in conn.execute("select season from core.season where platform <> 'sleeper' order by 1")]
    ids = name_map(conn, "draft")
    by_season = defaultdict(list)
    for row_number, d in workbook_rows(conn, "Draft"):
        if int(d["Year"]) in seasons:
            by_season[int(d["Year"])].append((row_number, d))

    problems, summary = [], []
    for season in seasons:
        picks = []
        for pick_no, (row_number, d) in enumerate(by_season.get(season, []), start=1):
            team = str(d.get("Drafting Team") or "").strip()
            code = None if team in ("", "?") else franchise_code_for(team, season)
            if team not in ("", "?") and not code:
                problems.append(f"Draft row {row_number}: don't recognize team '{team}'")
                continue
            name = clean_name(d.get("Player Name"))
            recorded = d.get("Overall Pick")
            picks.append((
                f"workbook-{season}", pick_no, season, int(d["Round"]), d.get("Pick"), code,
                ids.get((season, code, name)), to_bool(d.get("Keeper")), name,
                recorded is None or int(recorded) != pick_no,
            ))
        if problems or not picks:
            continue
        draft_id = f"workbook-{season}"
        conn.execute(
            """
            insert into core.draft (draft_id, season, draft_type, rounds, status, source)
            values (%s, %s, null, %s, 'complete', 'sheet')
            on conflict (draft_id) do update set rounds = excluded.rounds, source = 'sheet'
            """,
            (draft_id, season, max(p[3] for p in picks)),
        )
        conn.execute("delete from core.draft_pick where draft_id = %s", (draft_id,))
        with conn.cursor() as cur:
            cur.executemany(
                """
                insert into core.draft_pick (draft_id, pick_no, season, round, draft_slot, franchise_code,
                                             player_id, is_keeper, player_name_as_entered, pick_no_is_estimated)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                picks,
            )
        no_team = sum(1 for p in picks if p[5] is None)
        no_id = sum(1 for p in picks if p[6] is None)
        summary.append(f"{season}: {len(picks)} picks ({no_team} with no team, {no_id} with no player id yet)")
    if problems:
        raise LoadProblem("draft_2020", problems)
    for line in summary or ["no workbook-only drafts to load"]:
        print(f"    {line}")


def load_award_players(conn):
    ids = name_map(conn, "award")
    awards = conn.execute(
        "select award_id, season, franchise_code, player_name_as_entered from core.award "
        "where player_name_as_entered is not null").fetchall()
    updates = []
    for award_id, season, code, name in awards:
        updates.append((ids.get((season, code, clean_name(name))), award_id))
    with conn.cursor() as cur:
        cur.executemany("update core.award set player_id = %s where award_id = %s", updates)
    linked = sum(1 for pid, _ in updates if pid)
    print(f"    {linked} of {len(updates)} individual awards linked to a player"
          " (the rest: Most Valuable Owner rows, coaches, or names still unmatched)")


STEPS = [
    ("seasons", load_seasons),
    ("owners", load_owners),
    ("players", load_players),
    ("weeks", load_weeks),
    ("games", load_games),
    ("playoffs", load_playoffs),
    ("drafts", load_drafts),
    ("transactions", load_transactions),
    ("rankings", load_rankings),
    ("awards", load_awards),
    ("names", load_names),
    ("keepers", load_keepers),
    ("draft_2020", load_sheet_drafts),
    ("award_players", load_award_players),
]
STEP_NAMES = [name for name, _ in STEPS]


SCHEMA_TIP = (
    "Your database is missing a table or column this code needs, usually because a newer schema file\n"
    "hasn't been run yet. Run `python -m etl.run_sql --all`, then run this command again."
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the core tables from the raw layer.")
    parser.add_argument("--step", action="append", choices=STEP_NAMES,
                        help="run only this step (repeatable). Default: all, in order")
    parser.add_argument("--from", dest="start", choices=STEP_NAMES,
                        help="run this step and every step after it (e.g. --from names)")
    args = parser.parse_args()
    if args.step and args.start:
        parser.error("use --step or --from, not both")
    chosen = set(args.step or STEP_NAMES[STEP_NAMES.index(args.start) if args.start else 0:])

    with get_connection() as conn:
        data_changing(conn, "load_core")        # Phase 11: the app stops trusting cached results + copies now
        for name, step in STEPS:
            if name not in chosen:
                continue
            print(f"{name}...")
            try:
                step(conn)
                conn.commit()
            except LoadProblem as problem:
                conn.rollback()
                print(f"\nStopped at '{problem.step}'. Nothing from this step was saved. Problems:")
                for line in problem.problems[:40]:
                    print(f"  - {line}")
                if len(problem.problems) > 40:
                    print(f"  ...and {len(problem.problems) - 40} more")
                sys.exit(1)
            except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as error:
                conn.rollback()
                print(f"\nStopped at '{name}'. Nothing from this step was saved. The database said:\n  {error}")
                print(SCHEMA_TIP)
                sys.exit(1)
            except psycopg.Error as error:
                conn.rollback()
                print(f"\nStopped at '{name}'. Nothing from this step was saved. The database said:\n  {error}")
                print("Tip: run all the steps in order (no --step) so everything a step needs is already loaded.")
                sys.exit(1)

        data_changed(conn, "load_core")         # Phase 11: new data version + rebuilt precomputed copies
        try:
            summary = conn.execute("select check_name, problems from qa.summary where problems > 0").fetchall()
        except psycopg.errors.UndefinedTable:
            print("\nLoaded, but qa.summary is missing. " + SCHEMA_TIP)
            sys.exit(1)
    if summary:
        print("\nLoaded. These checks found something to look at (select * from qa.<check name>):")
        for check, count in summary:
            print(f"  {check}: {count}")
    else:
        print("\nLoaded. All checks in qa.summary are zero.")


if __name__ == "__main__":
    main()
