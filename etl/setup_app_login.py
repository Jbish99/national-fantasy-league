"""
etl/setup_app_login.py

Phase 5: switches on the read-only "app_reader" database login for the Streamlit app
and saves its connection string to .streamlit/secrets.toml (git-ignored).

Run from the project root, AFTER `python -m etl.run_sql --all` (that creates app_reader):
    python -m etl.setup_app_login

What it does:
  1. Makes a long random password and sets it on app_reader (connected as you, the owner).
  2. Builds app_reader's address from your pooled DATABASE_URL in .env
     (same Neon server and database, just a different user + password).
  3. Tests the new login: reading league history works, writing is blocked.
  4. Saves it as DATABASE_URL in .streamlit/secrets.toml. The app reads that file before .env,
     so `streamlit run app/streamlit_app.py` on your computer now uses the read-only login too.
Then paste the whole of .streamlit/secrets.toml into Streamlit Cloud's "Secrets" box.

Running it again makes a NEW password (the old one stops working right away), so paste the
new secrets.toml into Streamlit Cloud again afterwards (app Settings -> Secrets).
The password is never printed and never goes to GitHub.
"""

import os
import secrets
import sys
import time
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

from etl.db import PROJECT_ROOT, get_connection

ROLE = "app_reader"
SECRETS_FILE = PROJECT_ROOT / ".streamlit" / "secrets.toml"
GITIGNORE = PROJECT_ROOT / ".gitignore"


def app_url(pooled_url: str, password: str) -> str:
    """The pooled address with the owner's user:password swapped for app_reader's."""
    parts = urlsplit(pooled_url)
    host = parts.netloc.rsplit("@", 1)[-1]                      # drop "owner:password@"
    return urlunsplit(parts._replace(netloc=f"{ROLE}:{quote(password, safe='')}@{host}"))


def set_password(password: str) -> None:
    with get_connection() as conn:                               # you, the owner (direct connection)
        if not conn.execute("select 1 from pg_roles where rolname = %s", (ROLE,)).fetchone():
            sys.exit(f"The {ROLE} role doesn't exist yet.\n"
                     "  -> Run  python -m etl.run_sql --all  first (it creates the role), then run this again.")
        conn.execute(sql.SQL("alter role {} with login password {}")
                     .format(sql.Identifier(ROLE), sql.Literal(password)))
        conn.commit()


def connect_as_app(url: str) -> psycopg.Connection:
    """Connect with the new login. The pooler can take a few seconds to see a new password."""
    for attempt in range(1, 6):
        try:
            return psycopg.connect(url, connect_timeout=20)
        except psycopg.OperationalError as error:
            if attempt == 5:
                print("Couldn't log in as app_reader.\n"
                      f"  The database said: {str(error).strip().splitlines()[0]}\n"
                      "  Wait a minute and run this script again (it makes a fresh password).")
                sys.exit(1)
            time.sleep(3)


def check_read_only(url: str) -> int:
    """Reading works and writing is refused. Returns how many official games it can see."""
    with connect_as_app(url) as conn:
        games = conn.execute("select count(*) from core.game").fetchone()[0]
        conn.execute("select count(*) from core.v_lineup_week").fetchone()   # a view the app uses
        for statement in ("update core.season set num_teams = num_teams where false",
                          "create table core.app_reader_write_test (x int)"):
            try:
                with conn.transaction():
                    conn.execute(statement)
                    raise psycopg.Rollback()                      # if it ever got through, undo it
                sys.exit(f"STOP: app_reader was allowed to run  {statement}\n"
                         "  It should be read-only. Run  python -m etl.run_sql db/schema/010_app_reader.sql  "
                         "and send Claude this message.")
            except psycopg.errors.InsufficientPrivilege:
                pass                                              # refused, as it should be
    return games


def save_secret(url: str) -> None:
    if ".streamlit/secrets.toml" not in GITIGNORE.read_text(encoding="utf-8"):
        sys.exit(".gitignore doesn't list .streamlit/secrets.toml, so the password could end up on GitHub.\n"
                 "  -> Add the line  .streamlit/secrets.toml  to .gitignore, then run this again.")
    SECRETS_FILE.parent.mkdir(exist_ok=True)
    comments = ["# Read-only app login (made by etl/setup_app_login.py).",
                "# Paste this whole file into Streamlit Cloud: app Settings -> Secrets."]
    old = SECRETS_FILE.read_text(encoding="utf-8").splitlines() if SECRETS_FILE.exists() else []
    keep = [line for line in old                                  # anything else you added stays
            if not line.strip().startswith("DATABASE_URL") and line not in comments]
    header = comments + [f'DATABASE_URL = "{url}"']                # top-level keys must come before any [section]
    SECRETS_FILE.write_text("\n".join(header + keep).rstrip() + "\n", encoding="utf-8")


def main():
    pooled = os.getenv("DATABASE_URL", "").strip()
    if not pooled:
        sys.exit("No DATABASE_URL in .env. It should be Neon's POOLED address (host name has '-pooler').\n"
                 "  -> Copy it from Neon's Connect button with 'Connection pooling' on, then run this again.")
    if "-pooler" not in pooled:
        print("Note: DATABASE_URL in .env isn't Neon's pooled address (no '-pooler' in the host). Using it anyway.")

    password = secrets.token_urlsafe(24)                          # 32 letters/digits/-/_ ; about 190 bits
    print(f"1/3  Setting a new password on {ROLE} ...")
    set_password(password)
    url = app_url(pooled, password)

    print("2/3  Testing the read-only login ...")
    games = check_read_only(url)
    print(f"     ok: it can read the league history ({games} official games)")
    print("     ok: writing and creating tables are refused")

    print("3/3  Saving it to .streamlit/secrets.toml ...")
    save_secret(url)
    host = urlsplit(url).hostname or "(local socket)"
    print(f"\nDone. The app's database login is {ROLE} @ {host}.")
    print("Next: open .streamlit/secrets.toml in VS Code and copy everything in it into Streamlit Cloud's Secrets box.")


if __name__ == "__main__":
    main()
