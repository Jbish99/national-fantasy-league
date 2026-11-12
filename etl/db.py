"""
etl/db.py

Shared database connection for every ETL script.
Reads the Neon connection strings that `neon init` wrote to .env:
  - DATABASE_URL_UNPOOLED  direct connection, preferred for ETL loads
  - DATABASE_URL           pooled connection, used if the direct one is missing

Test it from the project root with:
    python -m etl.db
"""

import os
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()


def _connection_url() -> str:
    for name in ("DATABASE_URL_UNPOOLED", "DATABASE_URL"):
        url = os.getenv(name)
        if url and "USER:PASSWORD" not in url:
            return url
    sys.exit(
        "No database connection string found. Run `neon init` from the project "
        "root (it writes DATABASE_URL to .env), then try again."
    )


def get_connection() -> psycopg.Connection:
    """Open a new connection to the league database."""
    return psycopg.connect(_connection_url())


if __name__ == "__main__":
    with get_connection() as conn:
        row = conn.execute(
            "select current_database(), current_user, version()"
        ).fetchone()
    print("Connected!")
    print(f"  database: {row[0]}")
    print(f"  user:     {row[1]}")
    print(f"  server:   {row[2].split(',')[0]}")
