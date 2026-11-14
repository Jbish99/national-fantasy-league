"""
etl/db.py

Shared database connection for every ETL script.
Reads the Neon connection strings from .env in the project root
(or from environment variables, e.g. Codespaces secrets):
  - DATABASE_URL_UNPOOLED  direct connection, preferred for ETL loads
  - DATABASE_URL           pooled connection, used if the direct one is missing

Test it from the project root with:
    python -m etl.db
If it can't find a connection string, it explains what's wrong with .env.
"""

import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
URL_NAMES = ("DATABASE_URL_UNPOOLED", "DATABASE_URL")
PLACEHOLDER = "USER:PASSWORD"   # the fake value in .env.example

try:
    load_dotenv(ENV_FILE)
except UnicodeDecodeError:
    pass  # .env saved in the wrong encoding; _explain_missing_url() says how to fix it


def _connection_url() -> str:
    for name in URL_NAMES:
        url = os.getenv(name, "").strip()
        if url and PLACEHOLDER not in url:
            return url
    sys.exit(_explain_missing_url())


def _explain_missing_url() -> str:
    """Work out why no usable connection string was found, in plain English."""
    msg = ["Couldn't find a database connection string."]

    if not ENV_FILE.exists():
        msg.append(f"  There is no file named exactly .env in {PROJECT_ROOT}")
        lookalikes = sorted(
            p.name for p in PROJECT_ROOT.iterdir()
            if p.is_file() and "env" in p.name.lower() and p.name != ".env.example"
        )
        if lookalikes:
            msg.append(f"  Found these instead: {', '.join(lookalikes)}")
            msg.append("  -> Rename the one with your Neon strings to exactly .env (no .txt, no 'copy').")
        else:
            msg.append("  -> Copy .env.example, rename the copy to .env, and paste in your Neon strings.")
        return "\n".join(msg)

    raw = ENV_FILE.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        msg.append("  .env is saved as UTF-16, which Python can't read here.")
        msg.append("  -> In VS Code, open .env, click 'UTF-16 LE' in the bottom-right bar,")
        msg.append("     choose 'Save with Encoding', then 'UTF-8'.")
        return "\n".join(msg)

    text = raw.decode("utf-8-sig", errors="replace")
    found = [n for n in URL_NAMES if re.search(rf"^\s*(export\s+)?{n}\s*=", text, re.MULTILINE)]

    if not found:
        msg.append("  .env exists but has no DATABASE_URL or DATABASE_URL_UNPOOLED line.")
        msg.append("  -> Each line should start with the name exactly, e.g.  DATABASE_URL=postgresql://...")
    elif PLACEHOLDER in text:
        msg.append("  .env still has the placeholder values copied from .env.example.")
        msg.append("  -> Replace everything after the = with the real strings from Neon's Connect button.")
    else:
        msg.append("  .env has the DATABASE_URL line(s), but the values are empty.")
        msg.append("  -> Paste the full connection string right after the =, then save the file.")
    msg.append("  (Unsaved changes don't count. Check the file's tab has no dot on it in VS Code.)")
    return "\n".join(msg)


def get_connection() -> psycopg.Connection:
    """Open a new connection to the league database (or explain plainly why it can't)."""
    try:
        return psycopg.connect(_connection_url())
    except psycopg.OperationalError as error:
        reason = str(error).strip().splitlines()[0]
        print("Couldn't connect to the database.\n"
              f"  The database said: {reason}\n"
              "  Check: your internet connection, and that DATABASE_URL_UNPOOLED in .env is copied exactly\n"
              "  from Neon's Connect button (a 'password authentication failed' means it isn't).")
        sys.exit(1)


if __name__ == "__main__":
    with get_connection() as conn:
        row = conn.execute(
            "select current_database(), current_user, version()"
        ).fetchone()
    print("Connected!")
    print(f"  database: {row[0]}")
    print(f"  user:     {row[1]}")
    print(f"  server:   {row[2].split(',')[0]}")
