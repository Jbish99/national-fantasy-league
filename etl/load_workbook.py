"""
etl/load_workbook.py

Copies the official league history workbook (All-In-One.xlsx) into
raw.workbook_row, as-is: one database row per spreadsheet row, stored as
{column header: value}. Nothing is cleaned here; the core loader does that.

Only the data sheets are loaded. The Dashboard tabs are summaries of these,
so they're skipped. Blank rows (no value in the sheet's key column) are skipped.

Safe to re-run: each sheet's rows are replaced, all in one transaction.
When your brother updates the workbook, download it as .xlsx, replace
data/manual/All-In-One.xlsx, and run this again.

Run from the project root:
    python -m etl.load_workbook
    python -m etl.load_workbook --file "C:\\path\\to\\Other Copy.xlsx"
"""

import argparse
import hashlib
import sys
from datetime import date, datetime, time
from pathlib import Path

import openpyxl
from psycopg.types.json import Jsonb

from etl.db import PROJECT_ROOT, get_connection

DEFAULT_FILE = PROJECT_ROOT / "data" / "manual" / "All-In-One.xlsx"

# sheet name -> the column that must have a value for a row to count
DATA_SHEETS = {
    "Scores": "Game ID",
    "Draft": "Player Name",
    "Rankings": "Year",
    "Keepers": "Name",
    "Individual Awards": "Award",
    "Team Awards": "Award",
    "Rules": "Description",
}

INSERT_SQL = """
    insert into raw.workbook_row (workbook, sheet, row_number, data, file_sha256)
    values (%s, %s, %s, %s, %s)
"""


def to_json_value(value):
    """Make a cell value safe for JSON, without changing what it means."""
    if value is None:
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()          # e.g. records Excel turned into dates
    if isinstance(value, float) and value.is_integer():
        return int(value)                 # 2025.0 -> 2025, 97.0 -> 97
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value                          # int, float, bool


def read_sheet(ws, key_column):
    """Yield (excel_row_number, {header: value}) for every non-blank data row."""
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None) or ()
    columns = [(i, str(h).strip()) for i, h in enumerate(header) if h is not None and str(h).strip()]
    names = [name for _, name in columns]
    if key_column not in names:
        sys.exit(f"Sheet '{ws.title}' has no '{key_column}' column. Headers found: {names}")

    for excel_row, values in enumerate(rows, start=2):
        record = {name: to_json_value(values[i]) if i < len(values) else None for i, name in columns}
        if record.get(key_column) is None:
            continue
        yield excel_row, record


def main() -> None:
    parser = argparse.ArgumentParser(description="Load All-In-One.xlsx into raw.workbook_row.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="path to the workbook")
    args = parser.parse_args()

    path: Path = args.file
    if not path.exists():
        sys.exit(f"Can't find the workbook at {path}\n"
                 f"Put the .xlsx there, or pass --file with its location.")

    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    workbook_name = "All-In-One.xlsx"   # stable name, even if the file on disk is called something else
    print(f"Reading {path.name} (fingerprint {sha256[:12]})...")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    missing = [name for name in DATA_SHEETS if name not in wb.sheetnames]
    if missing:
        sys.exit(f"These sheets are missing from the workbook: {', '.join(missing)}")

    with get_connection() as conn:
        for sheet, key_column in DATA_SHEETS.items():
            rows = list(read_sheet(wb[sheet], key_column))
            conn.execute("delete from raw.workbook_row where workbook = %s and sheet = %s",
                         (workbook_name, sheet))
            with conn.cursor() as cur:
                cur.executemany(INSERT_SQL, [
                    (workbook_name, sheet, row_number, Jsonb(record), sha256)
                    for row_number, record in rows
                ])
            print(f"  {sheet:<18} {len(rows):>5} rows")
        conn.commit()

    wb.close()
    print("Done. Everything is in raw.workbook_row.")


if __name__ == "__main__":
    main()
