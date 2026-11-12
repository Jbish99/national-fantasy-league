"""
etl/pull_sleeper.py

Phase 2, step 1: save every Sleeper API response for every season into
raw.sleeper_response. Nothing is cleaned or reshaped here. That's step 2.

Starting from SLEEPER_CURRENT_LEAGUE_ID, it walks back through
previous_league_id and, for each season, saves:
  league, users, rosters, matchups (every scored week), transactions (weeks 1-18),
  winners_bracket, losers_bracket, traded_picks, drafts, each draft, and its picks.
It also saves /state/nfl and /players/nfl. The players file is big, so it's
kept in data/cache/ and only re-downloaded once it's a week old.

Safe to re-run: identical responses aren't stored twice; a response that
changed (stat correction, new week) is kept as a new version.

Run from the project root:
    python -m etl.pull_sleeper                    # all seasons + players
    python -m etl.pull_sleeper --season 2026      # just one season (repeatable)
    python -m etl.pull_sleeper --skip-players     # don't touch /players/nfl
    python -m etl.pull_sleeper --refresh-players  # force a fresh /players/nfl download
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests
from psycopg.types.json import Jsonb

from etl.db import PROJECT_ROOT, get_connection

BASE_URL = "https://api.sleeper.app/v1"
PAUSE_SECONDS = 0.2           # at most ~300 calls/min; Sleeper's limit is 1,000/min
MAX_TRANSACTION_WEEK = 18     # transactions are pulled for weeks 1-18
PLAYERS_CACHE = PROJECT_ROOT / "data" / "cache" / "players_nfl.json"
PLAYERS_MAX_AGE = timedelta(days=7)

SAVE_SQL = """
    insert into raw.sleeper_response (endpoint, request_url, season, week, payload)
    values (%s, %s, %s, %s, %s)
    on conflict (request_url, payload_hash) do update set last_pulled_at = now()
    returning (xmax = 0) as is_new
"""

session = requests.Session()
session.headers["User-Agent"] = "national-fantasy-league-history"


def fetch(path: str, timeout: int = 60):
    """GET a Sleeper endpoint. Retries politely on rate limits and server hiccups."""
    url = f"{BASE_URL}{path}"
    problem = None
    for attempt in range(1, 5):
        time.sleep(PAUSE_SECONDS)
        try:
            resp = session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            problem = exc
        else:
            if resp.status_code == 200:
                return url, resp.json()
            if resp.status_code not in (429, 500, 502, 503, 504):
                resp.raise_for_status()
            problem = f"HTTP {resp.status_code}"
        wait = 2 ** attempt
        print(f"    retrying {path} in {wait}s ({problem})")
        time.sleep(wait)
    raise RuntimeError(f"Gave up on {url} after 4 tries ({problem})")


class Saver:
    """Writes responses to raw.sleeper_response and counts what changed."""

    def __init__(self, conn):
        self.conn = conn
        self.new = 0      # first time seen, or the body changed
        self.same = 0     # identical to what we already had
        self.empty = 0    # Sleeper returned null (e.g. no bracket yet)

    def store(self, endpoint, url, payload, season=None, week=None):
        if payload is None:
            self.empty += 1
            return None
        is_new = self.conn.execute(
            SAVE_SQL, (endpoint, url, season, week, Jsonb(payload))
        ).fetchone()[0]
        if is_new:
            self.new += 1
        else:
            self.same += 1
        return payload

    def pull(self, endpoint, path, season=None, week=None):
        url, payload = fetch(path)
        return self.store(endpoint, url, payload, season, week)

    def counts(self):
        return (self.new, self.same, self.empty)


def pull_season(saver: Saver, league: dict) -> None:
    league_id = league["league_id"]
    season = int(league["season"])
    last_week = (league.get("settings") or {}).get("last_scored_leg") or 0
    if not last_week:
        print(f"    note: {season} has no scored weeks yet, skipping matchups")

    def pull(endpoint, path, week=None):
        return saver.pull(endpoint, path, season, week)

    pull("users", f"/league/{league_id}/users")
    pull("rosters", f"/league/{league_id}/rosters")
    for week in range(1, last_week + 1):
        pull("matchups", f"/league/{league_id}/matchups/{week}", week)
    for week in range(1, MAX_TRANSACTION_WEEK + 1):
        pull("transactions", f"/league/{league_id}/transactions/{week}", week)
    pull("winners_bracket", f"/league/{league_id}/winners_bracket")
    pull("losers_bracket", f"/league/{league_id}/losers_bracket")
    pull("traded_picks", f"/league/{league_id}/traded_picks")

    for draft in pull("drafts", f"/league/{league_id}/drafts") or []:
        draft_id = draft["draft_id"]
        pull("draft", f"/draft/{draft_id}")
        pull("draft_picks", f"/draft/{draft_id}/picks")


def pull_players(saver: Saver, refresh: bool) -> None:
    """Keep exactly one copy of /players/nfl in raw, refreshed at most weekly."""
    conn = saver.conn
    url = f"{BASE_URL}/players/nfl"
    in_raw = conn.execute(
        "select exists (select 1 from raw.sleeper_response where endpoint = 'players')"
    ).fetchone()[0]
    cache_fresh = (
        PLAYERS_CACHE.exists()
        and datetime.now() - datetime.fromtimestamp(PLAYERS_CACHE.stat().st_mtime) < PLAYERS_MAX_AGE
    )

    if cache_fresh and in_raw and not refresh:
        print("players: cached copy is less than a week old, skipping (--refresh-players to force)")
        return

    if refresh or not cache_fresh:
        print("players: downloading /players/nfl (big file, give it a moment)...")
        _, payload = fetch("/players/nfl", timeout=300)
        PLAYERS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PLAYERS_CACHE.write_text(json.dumps(payload), encoding="utf-8")
    else:
        print("players: loading the cached copy into the database")
        payload = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))

    saver.store("players", url, payload)
    # Only the newest copy is kept, so this big file doesn't eat the free storage.
    conn.execute("""
        delete from raw.sleeper_response
        where endpoint = 'players'
          and response_id <> (
              select response_id from raw.sleeper_response
              where endpoint = 'players'
              order by last_pulled_at desc, response_id desc
              limit 1)
    """)
    conn.commit()
    print(f"players: saved {len(payload):,} players")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Sleeper league history into raw.sleeper_response.")
    parser.add_argument("--season", type=int, action="append",
                        help="only pull this season's details (repeat for more)")
    parser.add_argument("--skip-players", action="store_true", help="don't touch /players/nfl")
    parser.add_argument("--refresh-players", action="store_true", help="force a fresh /players/nfl download")
    args = parser.parse_args()

    league_id = os.getenv("SLEEPER_CURRENT_LEAGUE_ID", "").strip()
    if not league_id:
        sys.exit("SLEEPER_CURRENT_LEAGUE_ID is missing. Add it to .env (see .env.example).")

    started = time.time()
    with get_connection() as conn:
        saver = Saver(conn)
        saver.pull("state", "/state/nfl")
        conn.commit()

        seen = set()
        while league_id and league_id != "0" and league_id not in seen:
            seen.add(league_id)
            url, league = fetch(f"/league/{league_id}")
            if league is None:
                sys.exit(f"Sleeper returned nothing for league {league_id}. Check SLEEPER_CURRENT_LEAGUE_ID.")
            season = int(league["season"])
            before = saver.counts()
            saver.store("league", url, league, season)

            if args.season and season not in args.season:
                print(f"{season}: skipped (not in --season)")
            else:
                print(f"{season}: pulling league {league_id}...")
                pull_season(saver, league)
                new, same, empty = (after - prior for after, prior in zip(saver.counts(), before))
                print(f"{season}: done. {new} new/changed, {same} unchanged, {empty} empty")
            conn.commit()
            league_id = league.get("previous_league_id")

        if not args.skip_players:
            pull_players(saver, args.refresh_players)

    minutes, seconds = divmod(int(time.time() - started), 60)
    print(f"\nAll done in {minutes}m {seconds}s. Totals: {saver.new} new/changed, "
          f"{saver.same} unchanged, {saver.empty} empty responses.")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        sys.exit(f"\nCouldn't reach Sleeper: {exc}\nCheck your internet connection and try again.")
