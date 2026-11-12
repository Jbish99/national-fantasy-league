# National Fantasy League: History Database & Dashboard

Historical database and Streamlit dashboard for the National Fantasy League (Sleeper, 2021 to present, plus the 2020 ESPN season).

**Stack:** PostgreSQL on Neon · Python ETL in VS Code · DBeaver for browsing/querying · Streamlit Community Cloud for the app.

---

## 1. Set up VS Code (one time per computer)

Install these first:
- [Python 3.11+](https://www.python.org/downloads/) (on Windows, tick **"Add python.exe to PATH"**)
- [Git](https://git-scm.com/downloads)
- [Node.js LTS](https://nodejs.org/) (only needed for the Neon CLI)
- [VS Code](https://code.visualstudio.com/) with the **Python** extension (by Microsoft)

Then, in VS Code:

1. **Clone:** `Ctrl+Shift+P` → **Git: Clone** → paste this repo's URL → pick a folder → **Open**.
2. **Make a virtual environment:** open a terminal (`` Ctrl+` ``) and run:
   ```
   python -m venv .venv
   ```
   VS Code will ask "use this environment for the workspace?" → **Yes**. (If it doesn't ask: `Ctrl+Shift+P` → **Python: Select Interpreter** → pick `.venv`.)
3. **Open a new terminal** (trash-can the old one, `` Ctrl+` `` again). The prompt should start with `(.venv)`.
4. **Install packages:**
   ```
   pip install -r requirements.txt
   ```

## 2. Connect to the database (one time per computer)

```
npm install -g neon@latest
neon login
neon init -y --agent vscode --org-id org-bitter-bird-10782369 --project-id cold-sea-50329849
```
`neon init` writes `DATABASE_URL` (pooled) and `DATABASE_URL_UNPOOLED` (direct) to `.env`. That file is git-ignored. Never commit it.

Add this line to `.env` too:
```
SLEEPER_CURRENT_LEAGUE_ID=1334317252626882560
```

Test the connection:
```
python -m etl.db
```
You should see `Connected!`.

## 3. Build the database (run once, safe to re-run)

In DBeaver (connected with the **unpooled** host, the one without `-pooler`), run these with **Execute SQL Script** (`Alt+X`), in order:

1. `db/schema/001_raw.sql`
2. `db/schema/002_core.sql`
3. `db/schema/003_views.sql`
4. `db/checks/001_validation.sql`
5. `db/seed/001_reference_data.sql`

Check: `select * from qa.summary;` → 14 rows, all `0`.

**Rebuilding after a schema change:** run `db/reset_core.sql` first, then the 5 files above, then the loaders again.
It drops only the `core` and `qa` schemas; everything already pulled into `raw` stays.

## 4. Load the raw data (safe to re-run anytime)

```
python -m etl.pull_sleeper
python -m etl.load_workbook
```
- `pull_sleeper` saves every Sleeper response for every season into `raw.sleeper_response` (a couple of minutes).
  Options: `--season 2026` (one season), `--skip-players`, `--refresh-players`.
- `load_workbook` copies the official history workbook (`data/manual/All-In-One.xlsx`) into `raw.workbook_row`.
  When the workbook is updated, download it as .xlsx, replace that file, and run it again.

Then run `db/checks/002_phase2_field_checks.sql` in DBeaver to see what came in from Sleeper.

## 5. Build the clean tables (safe to re-run anytime)

```
python -m etl.load_core
```
Fills every `core` table from the raw data, step by step (seasons, owners, players, weeks, games, playoffs,
drafts, transactions, rankings, awards), then prints any `qa` checks that found something.
Run one step on its own with `--step games` (repeatable), but run them all the first time.

Then in DBeaver: `select * from qa.summary;`
- `custom_points_used` = 2 is expected: Sleeper's commissioner override on the 2022 Fantasy Bowl.
- `stub_players` = a few is fine: player ids Sleeper's player list doesn't have anymore.
- Anything else non-zero: look at `select * from qa.<check_name>;`

Typical update after new games or a new workbook version: `pull_sleeper` → `load_workbook` → `load_core`.

## Where the official history comes from

- **`core.game`** is the official record of every game (2020 →). It's built from the All-In-One workbook.
  For seasons marked `sleeper` in `core.season.official_scores_from`, scores come from Sleeper (they include
  stat corrections) unless the workbook names a different winner. 2020 and 2023 use the workbook's scores.
- **`core.game_override`** (in the seed file) holds hand-decided results: the 2022 co-champions and the 2020
  consolation games where only the winner is known.
- **`core.team_week` / `core.player_week`** are exactly what Sleeper reports, for lineup and player analysis.

---

## Folder structure

```
.env                 secrets from `neon init` (git-ignored)
.env.example         template showing which secrets exist
requirements.txt     Python packages
db/schema/           numbered SQL files, run in order
db/checks/           validation views (qa schema): each returns only problem rows
db/seed/             hand-maintained reference data (franchises, seasons, keeper rules, result overrides)
db/reset_core.sql    drops core + qa for a clean rebuild (raw is kept)
etl/                 scripts that pull from the APIs and load the database
data/manual/         All-In-One.xlsx (the official league history workbook) and other hand-kept files
data/cache/          big downloads like Sleeper /players/nfl (git-ignored)
app/                 Streamlit app (coming in Phase 4)
```

## Build phases

1. Database + schema ✅
2. History backfill + validation ← in progress (core loader done; keepers + 2020 draft + player-name matching next)
3. NFL stats + player ID mapping
4. Streamlit first version
5. Deploy
6. Weekly in-season refresh
