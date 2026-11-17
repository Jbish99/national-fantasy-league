"""
etl/time_app.py  (Phase 11: measure first, then speed up)

Opens every page of the dashboard the way a visitor does, without a browser (Streamlit's AppTest), and times it
against the database the app uses: .streamlit/secrets.toml (the app's read-only login, same as the live site),
else DATABASE_URL in .env.
  - cold = nothing cached: the first visitor after the cache empties, so every query goes to the database
  - warm = the same page again with everything cached: what the next click costs
For every query it records: connect time (the app opens a new connection per query), query time (database work
+ network), convert time (Python turning rows into numbers), rows, and how long the database itself worked
(EXPLAIN ANALYZE, run once per query afterwards). It also adds up what each page sends to the browser: the page
itself (HTML, tables, charts) and the league images it shows.

Run from the project root (VS Code terminal, .venv active):
    python -m etl.time_app                  every page (about 1-3 minutes on Neon)
    python -m etl.time_app --page trade     only pages whose name contains "trade"
    python -m etl.time_app --repeat 3       open each page cold 3 times and report the middle time
    python -m etl.time_app --no-explain     skip the database-side timing (faster)

Prints a summary and writes data/cache/time_app_<date>_<time>.md (send that file to Claude).
For the "database was asleep" number, run it when nobody has used the site for 5+ minutes (Neon's free plan
pauses the database after 5 idle minutes).
Read-only: it runs only the app's own SELECTs, plus EXPLAIN ANALYZE of them.
"""

import argparse
import logging
import os
import re
import statistics
import sys
import time
import tomllib
import weakref
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from etl.db import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
ENTRY = APP / "streamlit_app.py"
REPORT_DIR = PROJECT_ROOT / "data" / "cache"
PAGE_TIMEOUT = 300          # seconds one page may take before AppTest gives up

# Extra page states worth timing: page file -> [(name, query parameter, SQL that picks a value)]
VARIANTS = {
    "views/trades.py": [("Trade analyzer: one trade", "trade",
                         "select transaction_id from core.v_trade "
                         "order by created_at desc nulls last, transaction_id desc limit 1")],
}

# What each page runs inside AppTest: the same setup as app/streamlit_app.py (page config + the league look),
# then the page file itself.
WRAPPER = """
import runpy
import streamlit as st
import lib
st.set_page_config(page_title="National Fantasy League", layout="wide")
lib.style()
runpy.run_path({path!r}, run_name="__main__")
"""


# ---------------------------------------------------------------------------
# Where the database is
# ---------------------------------------------------------------------------

def app_database_url():
    """The address the app itself would use: secrets.toml first (like lib._database_url), then .env."""
    secrets = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    if secrets.exists():
        try:
            url = tomllib.loads(secrets.read_text(encoding="utf-8")).get("DATABASE_URL")
            if url:
                return url, ".streamlit/secrets.toml (the app's read-only login, like the live site)"
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
            print(f"  (couldn't read .streamlit/secrets.toml: {error}; trying .env)")
    env = dotenv_values(PROJECT_ROOT / ".env")
    for name in ("DATABASE_URL", "DATABASE_URL_UNPOOLED"):
        url = (env.get(name) or os.getenv(name) or "").strip()
        if url and "USER:PASSWORD" not in url:
            return url, f".env {name}"
    sys.exit("No database address found: fill in DATABASE_URL in .env (Neon's Connect button, pooled). "
             "Don't run setup_app_login just for this: it changes the live app's password.")


def describe(url):
    info = psycopg.conninfo.conninfo_to_dict(url)
    return f"{info.get('user', '?')}@{info.get('host', '?')}/{info.get('dbname', '?')}"


# ---------------------------------------------------------------------------
# Timing every connection and query (patched into psycopg, so it sees whatever app/lib.py does)
# ---------------------------------------------------------------------------

RECORDS = []                                   # one dict per connect / query
STATE = {"page": None, "run": None, "on": False}
_record_of = weakref.WeakKeyDictionary()       # cursor -> its query record (for the fetch time)


def install_timers():
    conn_cls, cur_cls = psycopg.Connection, psycopg.Cursor
    real_connect = conn_cls.connect.__func__
    real_execute, real_fetchall = cur_cls.execute, cur_cls.fetchall

    def connect(cls, *args, **kwargs):
        start = time.perf_counter()
        conn = real_connect(cls, *args, **kwargs)
        if STATE["on"]:
            RECORDS.append({"kind": "connect", "page": STATE["page"], "run": STATE["run"],
                            "seconds": time.perf_counter() - start})
        return conn

    def execute(self, query, params=None, *args, **kwargs):
        start = time.perf_counter()
        result = real_execute(self, query, params, *args, **kwargs)
        if STATE["on"]:
            sql = query if isinstance(query, str) else query.as_string(self.connection) \
                if hasattr(query, "as_string") else str(query)
            record = {"kind": "query", "page": STATE["page"], "run": STATE["run"], "sql": sql,
                      "params": tuple(params) if isinstance(params, (list, tuple)) else params,
                      "query_s": time.perf_counter() - start, "fetch_s": 0.0,
                      "rows": max(self.rowcount, 0), "cols": len(self.description or [])}
            RECORDS.append(record)
            _record_of[self] = record
        return result

    def fetchall(self):
        start = time.perf_counter()
        rows = real_fetchall(self)
        record = _record_of.get(self)
        if record is not None:
            record["fetch_s"] += time.perf_counter() - start
            record["rows"] = len(rows)
        return rows

    conn_cls.connect = classmethod(connect)
    psycopg.connect = conn_cls.connect
    cur_cls.execute, cur_cls.fetchall = execute, fetchall


def objects_in(sql):
    """'core.v_trade_side, core.v_trade_asset_value' -- the tables / views a query reads."""
    seen = []
    for name in re.findall(r"\b((?:core|qa|raw)\.[a-z_0-9]+)", sql):
        if name not in seen:
            seen.append(name)
    return ", ".join(seen[:3]) + (f" +{len(seen) - 3}" if len(seen) > 3 else "") if seen else \
        " ".join(sql.split())[:50]


def query_key(record):
    return (" ".join(record["sql"].split()), repr(record["params"]))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@dataclass
class Page:
    name: str
    file: str                       # e.g. views/home.py (relative to app/)
    params: dict = field(default_factory=dict)
    cold: list = field(default_factory=list)      # seconds per cold run
    warm: float = None
    payload_kb: float = 0.0
    images: dict = field(default_factory=dict)    # image path -> KB
    problems: list = field(default_factory=list)


def pages_in_app():
    """Every page listed in app/streamlit_app.py (so pages added later get timed too), in menu order."""
    source = ENTRY.read_text(encoding="utf-8")
    found = re.findall(r'st\.Page\(\s*"(views/[^"]+)"\s*,\s*title="([^"]+)"', source)
    if not found:
        sys.exit(f"Couldn't find the page list in {ENTRY}.")
    return [Page(title, path) for path, title in found]


def add_variants(pages, url):
    out = []
    for page in pages:
        out.append(page)
        for name, param, sql in VARIANTS.get(page.file, []):
            try:
                with psycopg.connect(url) as conn:
                    row = conn.execute(sql).fetchone()
            except psycopg.Error as error:
                print(f"  (skipping '{name}': {str(error).splitlines()[0]})")
                continue
            if row:
                out.append(Page(name, page.file, {param: str(row[0])}))
    return out


def walk(node):
    yield node
    children = getattr(node, "children", None)
    if isinstance(children, dict):
        for child in children.values():
            yield from walk(child)


def page_weight(at, page):
    """KB of page content sent to the browser, and the league images it shows (unique, KB on disk)."""
    size, refs = 0, set()
    for node in walk(at._tree):
        proto = getattr(node, "proto", None)
        if proto is None or not hasattr(proto, "SerializeToString"):
            continue
        data = proto.SerializeToString()
        size += len(data)
        refs |= {r.decode() for r in re.findall(rb"app/static/[A-Za-z0-9_./%-]+", data)}
    page.payload_kb = size / 1024
    for ref in sorted(refs):
        path = APP / ref[len("app/"):]
        if path.is_file():
            page.images[ref] = path.stat().st_size / 1024


def problems_of(at):
    found = []
    for kind in ("exception", "error"):
        try:
            for element in getattr(at, kind):
                message = getattr(element, "message", None) or getattr(element, "value", "")
                found.append(f"{kind}: {str(message).splitlines()[0][:150]}")
        except Exception:                   # an element type this Streamlit version names differently
            pass
    return found


def reset_app_memory():
    """Forget what app/lib.py keeps in memory besides Streamlit's caches (Phase 11: the data version it last
    read, the franchise names), so a cold run really starts cold. Works with older lib.py versions too."""
    import lib
    state = getattr(lib, "_STATE", None)
    if isinstance(state, dict):
        state.update(checked=-1e9, version=None, copies={})
    names = getattr(lib, "_FRANCHISES", None)
    if isinstance(names, dict):
        names.update(key=None, names=None)


def time_page(page, url, repeat):
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    script = WRAPPER.format(path=str(APP / page.file))
    at = None
    for i in range(repeat):
        st.cache_data.clear()
        st.cache_resource.clear()            # also drops the app's kept connections (Phase 11)
        reset_app_memory()
        at = AppTest.from_string(script, default_timeout=PAGE_TIMEOUT)
        at.secrets["DATABASE_URL"] = url
        for key, value in page.params.items():
            at.query_params[key] = value
        STATE.update(page=page.name, run=f"cold{i}", on=True)
        start = time.perf_counter()
        try:
            at.run()
        except Exception as error:          # e.g. the page took longer than PAGE_TIMEOUT
            page.problems.append(f"run failed: {str(error).splitlines()[0][:150]}")
            STATE["on"] = False
            return
        page.cold.append(time.perf_counter() - start)
    STATE.update(run="warm")
    start = time.perf_counter()
    at.run()
    page.warm = time.perf_counter() - start
    STATE["on"] = False
    page.problems += problems_of(at)
    page_weight(at, page)


def apptest_overhead(url):
    """AppTest's own start-up cost for a fresh session (an empty page with the league look), taken off every
    cold time: a real visitor doesn't pay it."""
    from streamlit.testing.v1 import AppTest

    script = WRAPPER.split("runpy.run_path")[0]
    times = []
    for _ in range(3):
        at = AppTest.from_string(script, default_timeout=PAGE_TIMEOUT)
        at.secrets["DATABASE_URL"] = url
        start = time.perf_counter()
        at.run()
        times.append(time.perf_counter() - start)
    return statistics.median(times)


# ---------------------------------------------------------------------------
# Database wake-up and database-side timing
# ---------------------------------------------------------------------------

def probe(url):
    """First connection (wakes Neon if it was asleep), a second one (normal cost), one tiny round trip."""
    out = {}
    for label in ("first", "next"):
        start = time.perf_counter()
        with psycopg.connect(url) as conn:
            connected = time.perf_counter()
            conn.execute("select 1").fetchone()
            out[label] = (connected - start, time.perf_counter() - connected)
            if label == "next":
                out["size"] = conn.execute("select pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
                out["version"] = conn.execute("show server_version").fetchone()[0]
    return out


def explain_all(url, records):
    """Database-side seconds (planning + execution) per distinct query, from EXPLAIN ANALYZE."""
    server = {}
    todo = {}
    for r in records:
        if r["kind"] == "query" and r["run"] == "cold0" and re.match(r"\s*(select|with)\b", r["sql"], re.I):
            todo.setdefault(query_key(r), r)
    print(f"Asking the database how long each of the {len(todo)} distinct queries takes (EXPLAIN ANALYZE)...")
    with psycopg.connect(url) as conn:
        conn.execute("set statement_timeout = '120s'")
        for key, r in todo.items():
            try:
                cur = psycopg.ClientCursor(conn)
                cur.execute("explain (analyze, format json) " + r["sql"], r["params"])
                plan = cur.fetchone()[0]
                plan = plan[0] if isinstance(plan, list) else plan
                server[key] = (plan.get("Planning Time", 0) + plan.get("Execution Time", 0)) / 1000
            except psycopg.Error as error:
                conn.rollback()
                server[key] = None
                print(f"  (couldn't explain {objects_in(r['sql'])}: {str(error).splitlines()[0][:100]})")
    return server


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def median(values):
    return statistics.median(values) if values else None


def summarize(pages, server, overhead):
    """Per page: the numbers from its first cold run (query details) + median cold time, AppTest's start-up
    cost taken off."""
    rows = []
    for page in pages:
        mine = [r for r in RECORDS if r["page"] == page.name and r["run"] == "cold0"]
        queries = [r for r in mine if r["kind"] == "query"]
        connects = [r for r in mine if r["kind"] == "connect"]
        warm_queries = sum(1 for r in RECORDS if r["page"] == page.name and r["run"] == "warm"
                           and r["kind"] == "query")
        connect_s = sum(r["seconds"] for r in connects)
        query_s = sum(r["query_s"] for r in queries)
        fetch_s = sum(r["fetch_s"] for r in queries)
        known = [server.get(query_key(r)) for r in queries]
        server_s = sum(s for s in known if s is not None) if server else None
        cold = max(median(page.cold) - overhead, 0) if page.cold else None
        first_cold = max(page.cold[0] - overhead, 0) if page.cold else None
        rows.append({
            "page": page.name, "cold": cold, "warm": page.warm, "queries": len(queries), "connects": len(connects),
            "connect_s": connect_s, "query_s": query_s, "server_s": server_s, "fetch_s": fetch_s,
            "python_s": max(first_cold - connect_s - query_s - fetch_s, 0) if first_cold is not None else None,
            "rows": sum(r["rows"] for r in queries), "warm_queries": warm_queries, "payload_kb": page.payload_kb,
            "n_images": len(page.images), "images_kb": sum(page.images.values()), "problems": page.problems,
        })
    return rows


def slowest_queries(server, limit=15):
    """Distinct queries (first cold run), slowest first, with the pages that use them."""
    grouped = {}
    for r in RECORDS:
        if r["kind"] != "query" or r["run"] != "cold0":
            continue
        g = grouped.setdefault(query_key(r), {"sql": r["sql"], "params": r["params"], "times": [], "pages": [],
                                              "rows": r["rows"], "cols": r["cols"], "fetch": []})
        g["times"].append(r["query_s"])
        g["fetch"].append(r["fetch_s"])
        if r["page"] not in g["pages"]:
            g["pages"].append(r["page"])
    out = []
    for key, g in grouped.items():
        out.append({**g, "query_s": median(g["times"]), "fetch_s": median(g["fetch"]),
                    "server_s": server.get(key), "what": objects_in(g["sql"])})
    out.sort(key=lambda q: -(q["query_s"] + q["fetch_s"]))
    return out[:limit] if limit else out


def fmt(value, digits=2, width=6):
    return f"{value:>{width}.{digits}f}" if value is not None else f"{'-':>{width}}"


def print_report(meta, rows, queries):
    print()
    print(f"Timed {len(rows)} pages against {meta['db']} ({meta['source']})")
    p = meta["probe"]
    print(f"AppTest's own start-up ({meta['overhead']:.2f} s per fresh session) is taken off the cold times.")
    print(f"Database wake-up: first connection {p['first'][0]:.2f} s, next connection {p['next'][0]:.2f} s, "
          f"one tiny query {p['next'][1] * 1000:.0f} ms.  Database size {p['size']}, Postgres {p['version']}.")
    if p["first"][0] > p["next"][0] + 0.5:
        print("  -> the first connection was slower: the database was probably asleep and had to wake up.")
    print()
    print("PAGES  (seconds; cold = nothing cached, warm = cached; KB = sent to the browser)")
    head = (f"{'page':<28}{'cold':>6}{'warm':>6}{'queries':>8}{'connect':>8}{'query':>7}{'db':>6}"
            f"{'python':>7}{'page KB':>8}{'images':>7}{'img KB':>7}")
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['page'][:27]:<28}{fmt(r['cold'])}{fmt(r['warm'])}{r['queries']:>8}{fmt(r['connect_s'], 2, 8)}"
              f"{fmt(r['query_s'], 2, 7)}{fmt(r['server_s'])}{fmt(r['python_s'], 2, 7)}{r['payload_kb']:>8.0f}"
              f"{r['n_images']:>7}{r['images_kb']:>7.0f}")
    ok = [r for r in rows if r["cold"] is not None]
    if ok:
        total = {k: sum((r[k] or 0) for r in ok) for k in ("connect_s", "query_s", "fetch_s", "python_s")}
        all_cold = sum(r["cold"] for r in ok) or 1
        server_total = sum((r["server_s"] or 0) for r in ok)
        print("-" * len(head))
        if meta["explained"]:
            where = (f"database work {server_total:.1f} s, network/transfer "
                     f"{max(total['query_s'] - server_total, 0):.1f} s")
        else:
            where = f"database + network {total['query_s']:.1f} s"
        print(f"Cold, all pages: {all_cold:.1f} s = connecting {total['connect_s']:.1f} s, {where}, "
              f"Python {total['fetch_s'] + total['python_s']:.1f} s.")
        print(f"Median page: cold {statistics.median(r['cold'] for r in ok):.2f} s, warm "
              f"{statistics.median(r['warm'] for r in ok if r['warm'] is not None):.2f} s.")
    warm_q = [r["page"] for r in rows if r["warm_queries"]]
    if warm_q:
        print(f"Pages that still query the database when warm: {', '.join(warm_q)}")
    print()
    print("SLOWEST QUERIES  (distinct, cold; query = database + network, db = the database alone)")
    head = f"{'query':>6}{'db':>6}{'rows':>8}{'cols':>5}  {'pages':<24}reads"
    print(head)
    print("-" * 100)
    for q in queries:
        pages = ", ".join(q["pages"]) if len(q["pages"]) <= 2 else f"{q['pages'][0]} +{len(q['pages']) - 1}"
        print(f"{fmt(q['query_s'] + q['fetch_s'])}{fmt(q['server_s'])}{q['rows']:>8}{q['cols']:>5}  "
              f"{pages[:23]:<24}{q['what'][:60]}")
    problems = [(r["page"], p) for r in rows for p in r["problems"]]
    if problems:
        print()
        print("PROBLEMS (pages that showed an error while being timed)")
        for page, problem in problems:
            print(f"  {page}: {problem}")


def write_markdown(meta, rows, all_queries, pages):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"time_app_{meta['when'].strftime('%Y-%m-%d_%H%M')}.md"
    p = meta["probe"]
    lines = [
        f"# Dashboard timing, {meta['when'].strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- Database: `{meta['db']}` from {meta['source']}; Postgres {p['version']}, size {p['size']}",
        f"- First connection {p['first'][0]:.2f} s (includes waking the database if it was asleep), next "
        f"connection {p['next'][0]:.2f} s, tiny query {p['next'][1] * 1000:.0f} ms",
        f"- Cold runs per page: {meta['repeat']} (median shown); AppTest's own start-up "
        f"({meta['overhead']:.2f} s per fresh session, measured on an empty page) is taken off cold times.",
        f"- Python {sys.version.split()[0]}, Streamlit {meta['streamlit']}, psycopg {psycopg.__version__}, "
        f"git commit {meta['commit']}.",
        "- cold = nothing cached; warm = same page again, cached. connect = opening connections (the app opened "
        "one per query before Phase 11); query = database + network; db = the database alone (EXPLAIN ANALYZE); python = everything else "
        "(converting rows, pandas, building the page). KB = page content sent to the browser; images = league "
        "images the page shows (unique, KB on disk).",
        "",
        "## Pages",
        "",
        "| Page | Cold s | Warm s | Queries | Connect s | Query s | DB s | Python s | Rows | Page KB | Images | "
        "Image KB | Warm queries |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(f"| {r['page']} | {fmt(r['cold']).strip()} | {fmt(r['warm']).strip()} | {r['queries']} | "
                     f"{r['connect_s']:.2f} | {r['query_s']:.2f} | {fmt(r['server_s']).strip()} | "
                     f"{fmt(r['python_s']).strip()} | {r['rows']:,} | {r['payload_kb']:.0f} | {r['n_images']} | "
                     f"{r['images_kb']:.0f} | {r['warm_queries']} |")
    lines += ["", "## Every distinct query (first cold run, slowest first)", "",
              "| Query s | Convert s | DB s | Rows | Cols | Pages | Reads | SQL (start) |",
              "|---:|---:|---:|---:|---:|---|---|---|"]
    for q in all_queries:
        sql = " ".join(q["sql"].split())[:160].replace("|", "/")
        lines.append(f"| {q['query_s']:.3f} | {q['fetch_s']:.3f} | {fmt(q['server_s'], 3).strip()} | {q['rows']:,} | "
                     f"{q['cols']} | {', '.join(q['pages'])} | {q['what']} | `{sql}` |")
    lines += ["", "## Images per page", ""]
    for page in pages:
        if page.images:
            biggest = sorted(page.images.items(), key=lambda kv: -kv[1])[:5]
            lines.append(f"- **{page.name}**: {len(page.images)} images, {sum(page.images.values()):.0f} KB "
                         f"(biggest: {', '.join(f'{Path(k).parent.name}/{Path(k).name} {v:.0f} KB' for k, v in biggest)})")
    problems = [(r["page"], p) for r in rows for p in r["problems"]]
    if problems:
        lines += ["", "## Problems", ""] + [f"- {page}: {problem}" for page, problem in problems]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def git_commit():
    head = PROJECT_ROOT / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            ref_path = PROJECT_ROOT / ".git" / ref[5:]
            if ref_path.exists():
                return ref_path.read_text().strip()[:7]
            packed = PROJECT_ROOT / ".git" / "packed-refs"
            for line in packed.read_text().splitlines():
                if line.endswith(ref[5:]):
                    return line[:7]
        return ref[:7]
    except OSError:
        return "?"


def main():
    parser = argparse.ArgumentParser(description="Time every dashboard page against the app's database.")
    parser.add_argument("--page", help="only pages whose name contains this text (case doesn't matter)")
    parser.add_argument("--repeat", type=int, default=1, help="cold runs per page (median reported)")
    parser.add_argument("--no-explain", action="store_true", help="skip EXPLAIN ANALYZE (database-side times)")
    args = parser.parse_args()

    sys.path.insert(0, str(APP))                 # the pages import lib / story from app/
    logging.disable(logging.WARNING)             # AppTest's "missing ScriptRunContext" chatter
    import streamlit
    try:
        streamlit.logger.set_log_level("error")
    except Exception:
        pass

    # Load the app's modules once before timing, like the live server does when it starts (otherwise the first
    # page timed would carry a second or so of one-time imports).
    import altair  # noqa: F401
    import lib  # noqa: F401
    import story  # noqa: F401

    url, source = app_database_url()
    print(f"Database: {describe(url)}  ({source})")
    print("Waking / timing the database connection...")
    meta = {"db": describe(url), "source": source, "when": datetime.now(), "repeat": max(1, args.repeat),
            "streamlit": streamlit.__version__, "commit": git_commit(), "probe": probe(url)}

    meta["overhead"] = apptest_overhead(url)
    install_timers()
    pages = add_variants(pages_in_app(), url)
    if args.page:
        pages = [p for p in pages if args.page.lower() in p.name.lower()]
        if not pages:
            sys.exit(f"No page name contains '{args.page}'.")
    for n, page in enumerate(pages, 1):
        print(f"  [{n:>2}/{len(pages)}] {page.name}...", end="", flush=True)
        time_page(page, url, meta["repeat"])
        cold = max(median(page.cold) - meta["overhead"], 0) if page.cold else None
        print(f" cold {cold:.2f} s, warm {page.warm:.2f} s" if cold is not None and page.warm is not None
              else " failed (see PROBLEMS below)")

    server = {} if args.no_explain else explain_all(url, RECORDS)
    meta["explained"] = bool(server)
    rows = summarize(pages, server, meta["overhead"])
    print_report(meta, rows, slowest_queries(server))
    path = write_markdown(meta, rows, slowest_queries(server, limit=None), pages)
    print()
    print(f"Full report: {path.relative_to(PROJECT_ROOT)}  (send this file to Claude)")


if __name__ == "__main__":
    main()
