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

## 3. Build the database (safe to re-run)

From the VS Code terminal:
```
python -m etl.run_sql --all
```
That runs every SQL file in order: `db/schema/001` → `007`, then `db/checks/001_validation.sql`, then
`db/seed/001_reference_data.sql`. Each file shows `ok`. Run it again **after every `git pull`** that changed
a `.sql` file. It's harmless when nothing changed.

Use this instead of opening the files in DBeaver. DBeaver saves its own copy of a file when it runs it, which
makes the next `git pull` fail with "your local changes would be overwritten". DBeaver is still the place to
browse the data and write your own queries (in a new SQL script, not one of the project files).

Check: `select * from qa.summary;` lists every check with its problem count.

**Rebuilding from scratch:** `python -m etl.run_sql --reset --all` drops only the `core` and `qa` schemas
(it asks first; everything pulled into `raw` stays), then rebuilds them. Run the loaders again afterwards.

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
drafts, transactions, rankings, awards, names, keepers, draft_2020, award_players), then prints any `qa`
checks that found something. Run one step with `--step games` (repeatable), or a step and everything after
it with `--from names`. Run them all the first time.

Then in DBeaver: `select * from qa.summary;`
- `custom_points_used` = 2 is expected: Sleeper's commissioner override on the 2022 Fantasy Bowl.
- `stub_players` = a few is fine: player ids Sleeper's player list doesn't have anymore.
- `unmatched_names` / `keepers_not_loaded`: workbook player names that need a player id (next section).
- Anything else non-zero: look at `select * from qa.<check_name>;`

Typical update after new games or a new workbook version: `pull_sleeper` → `load_workbook` → `load_core`.

## 6. Review the workbook's player names (keepers, 2020 draft, awards)

The workbook types player names by hand ("Saquon Barkely", "Gay", "Colts"), so the `names` step matches each
one to a Sleeper player id and writes the results to **`data/manual/player_name_map.csv`**. Open it in Excel.
Rows are sorted so the ones that need you are on top:

| status | meaning | what to do |
|---|---|---|
| `unmatched` | no good match, **not used** | type the right `player_id` (the `candidates` column usually has it) |
| `check` | close spelling or small doubt, **used** | glance at `matched_name`; fix `player_id` if it's wrong |
| `auto` | exact match, used | nothing |
| `confirmed` | you decided; kept on every re-run | nothing |
| `not_a_player` | e.g. a coach in Bozo of the Year | set this status yourself; never used |

- Typing a `player_id` is enough; it becomes `confirmed` on the next run. To look up an id in DBeaver:
  `select player_id, full_name, position, nfl_team from core.player where full_name ilike '%barkley%';`
- Use the `your_notes` column for anything you want to remember; it's always kept.
- Save as CSV (either CSV type works), close Excel, then run `python -m etl.load_core --from names`.
- Commit the CSV when you're happy with it, so your decisions are saved in GitHub.

## 7. Check the history against the workbook's dashboards

```
python -m etl.compare_dashboards
```
Compares the Scores Dashboard (head-to-head, season totals, record lists, Game of the Week, International,
IST, division and conference records) with the database. Differences caused by the score policy below are
**explained** (add `--details` to list them); anything **unexplained** is printed and worth a look.

## 8. NFL stat lines, games and weather (Phase 3)

```
python -m etl.load_nflverse
```
Downloads from nflverse (free, no key) and fills:
- `core.player_xref`: each Sleeper player's NFL (gsis) id
- `core.nfl_game`: every NFL game since 2020 with scores, roof, surface, temperature and wind
- `core.nfl_player_week`: weekly stat lines for every QB, RB, WR, TE, K and FB
- `core.nfl_team_week`: each NFL team's weekly defense / special-teams stat line (the real stat line behind
  Sleeper's DEF players), plus the yards its opponent gained
- `core.v_player_week_full`: every rostered Sleeper player-week next to the real stat line and game conditions
- `core.v_def_week_full`: every rostered Sleeper defense-week next to that team's stat line and points allowed

The downloaded files are saved in `data/cache/nflverse/` (git-ignored) and logged in `raw.nflverse_pull`.
Each run re-downloads only what can still change (the ID crosswalk, the schedule, this season's stats).
Options: `--refresh` (re-download everything), `--no-download` (rebuild from the saved files),
`--step xref|games|stats|teams`.

**Checking the NFL ids with points.** `core.v_nfl_league_points` scores every stat line with that season's
league rules (from Sleeper's scoring settings). When a player's NFL id is right, it matches Sleeper's points:
- `select * from qa.league_points_accuracy;` how often rebuilt points equal Sleeper's, by season
- `select * from qa.nfl_id_suspect;` players whose points mostly don't match (probably a wrong id)
- `select * from qa.league_points_vs_sleeper where abs(diff) > 1;` the individual weeks that differ

If `qa.league_players_without_nfl_id` or `qa.nfl_id_suspect` lists someone, add a row to `data/manual/nfl_id_overrides.csv`
(`player_id` = Sleeper id, `gsis_id` = NFL id, `note`), then run `python -m etl.load_nflverse --no-download`.
`python -m etl.check_nflverse` prints what nflverse's files contain, if a column ever goes missing.

## Where the official history comes from

- **`core.game`** is the official record of every game (2020 →). It's built from the All-In-One workbook.
  For seasons marked `sleeper` in `core.season.official_scores_from`, scores come from Sleeper (they include
  stat corrections) unless the workbook names a different winner. 2020 and 2023 use the workbook's scores.
- **`core.game_override`** (in the seed file) holds hand-decided results: the 2022 co-champions (a tie in
  head-to-head records) and the 2020 consolation games where only the winner is known (no score is stored;
  the workbook's 110-100 style numbers for those games are placeholders).
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
2. History backfill + validation ✅ (once the player-name review is done)
3. NFL stats + player ID mapping ✅ (rebuilding DEF fantasy points is parked for Advanced Statistics)
4. Streamlit first version ← next
5. Deploy
6. Weekly in-season refresh
