"""
etl/keep_warm.py  (Phase 11: keep-warm visits; Jaden chose them 2026-11-16)

Opens every page of the live app in a headless browser, the way a friend would, so that:
  - the app doesn't fall asleep (Streamlit Community Cloud puts an app to sleep after 12 hours without a visit;
    the next visitor then has to click "Yes, get this app back up!" and wait), and
  - every page's queries are already cached with the newest data when the first friend shows up.
If the app is asleep, it clicks the wake-up button and waits.

GitHub runs it after every daily refresh and again about 12 hours later (.github/workflows/keep_warm.yml).
Each run also times every page in a real browser, twice (first visit, then again with everything cached), and
puts the table on the run's summary page: the live-site numbers for claude/roadmap.md.

Needs only Playwright (not the rest of the project's packages):
    pip install playwright
    python -m playwright install chromium
    python -m etl.keep_warm                                   # the live app
    APP_URL=http://localhost:8501 python -m etl.keep_warm     # a local `streamlit run` (to test)

Only works for an app anyone with the link can open. A private app (sign-in required) is reported and skipped:
this script never signs in.
"""

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

APP_URL = os.getenv("APP_URL", "https://national-fantasy-league.streamlit.app").rstrip("/")
ENTRY = Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py"
WAKE_TIMEOUT = 300          # seconds a sleeping app may take to come back
PAGE_TIMEOUT = 120          # seconds one page may take
ON_GITHUB = os.getenv("GITHUB_ACTIONS") == "true"

READY = "[data-testid='stApp'][data-test-script-state='notRunning']"
CONNECTED = "[data-testid='stApp'][data-test-connection-state='CONNECTED']"
CONTENT = "[data-testid='stMainBlockContainer'] [data-testid='stElementContainer']"
WAKE_BUTTON = re.compile(r"get this app back up", re.I)
PRIVATE_TEXT = re.compile(r"sign in|log in|don.t have access|do not have access|request access", re.I)


def note(level, message):
    """Print; on GitHub also pin it to the run's summary page."""
    print(f"::{level}::{message}" if ON_GITHUB else f"{level.upper()}: {message}", flush=True)


def pages():
    """(title, url path) for every page in the app's menu, read from app/streamlit_app.py."""
    source = ENTRY.read_text(encoding="utf-8")
    out = []
    for call in re.findall(r"st\.Page\((.*?)\)", source, re.S):
        path = re.search(r'"views/([^"]+)\.py"', call)
        title = re.search(r'title="([^"]+)"', call)
        if not (path and title):
            continue
        url_path = re.search(r'url_path="([^"]+)"', call)
        if "default=True" in call:
            slug = ""
        elif url_path:
            slug = url_path.group(1)
        else:
            slug = path.group(1)                     # Streamlit names a page after its file
        out.append((title.group(1), slug))
    return out


def app_frame(page):
    """The frame that holds the Streamlit app (Community Cloud wraps the app in an iframe), or None."""
    for frame in page.frames:
        try:
            if frame.query_selector("[data-testid='stApp']"):
                return frame
        except PlaywrightError:
            continue
    return None


def looks_private(page):
    """A sign-in page instead of the app: we were sent to another site (Streamlit's sign-in at share.streamlit.io)
    or the page asks to sign in."""
    url = urlparse(page.url)
    if url.hostname and url.hostname != urlparse(APP_URL).hostname and \
            ("auth" in url.path or "login" in url.path or url.hostname.endswith("streamlit.io")):
        return True
    return bool(url.hostname and url.hostname != urlparse(APP_URL).hostname
                and PRIVATE_TEXT.search(page.inner_text("body")))


def wait_for_app(page, timeout):
    """Wait until the app has connected and finished running the page. Returns 'ready', 'asleep', 'private'
    or 'timeout'."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        frame = app_frame(page)
        if frame is not None:
            try:
                if frame.query_selector(CONNECTED) and frame.query_selector(READY) and frame.query_selector(CONTENT):
                    return "ready"
            except PlaywrightError:
                pass
        else:
            try:
                if page.get_by_role("button", name=WAKE_BUTTON).count():
                    return "asleep"
                if looks_private(page):
                    return "private"
            except PlaywrightError:
                pass
        page.wait_for_timeout(500)
    return "timeout"


def visit(page, url):
    """Open one page and wait for it. Returns (status, seconds, problems shown on the page)."""
    start = time.monotonic()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 1000)
    except PlaywrightError as error:
        return f"couldn't open: {str(error).splitlines()[0]}", time.monotonic() - start, []
    status = wait_for_app(page, PAGE_TIMEOUT)
    if status == "asleep":
        print("  The app is asleep: clicking 'Yes, get this app back up!' and waiting...", flush=True)
        page.get_by_role("button", name=WAKE_BUTTON).first.click()
        status = wait_for_app(page, WAKE_TIMEOUT)
        if status == "ready":
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 1000)   # the page we wanted
            start = time.monotonic()
            status = wait_for_app(page, PAGE_TIMEOUT)
    seconds = time.monotonic() - start
    problems = []
    frame = app_frame(page)
    if frame is not None:
        for selector in ("[data-testid='stException']", "[data-testid='stAlertContentError']"):
            for element in frame.query_selector_all(selector):
                problems.append(" ".join(element.inner_text().split())[:160])
    return status, seconds, problems


def main():
    targets = pages()
    print(f"Visiting {len(targets)} pages of {APP_URL}", flush=True)
    rows, broken = [], []
    with sync_playwright() as p:
        chrome = os.getenv("CHROME_PATH")
        browser = p.chromium.launch(headless=True, executable_path=chrome) if chrome else p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2,  # a phone
                                user_agent="Mozilla/5.0 (keep-warm visit; National Fantasy League GitHub workflow)")
        for title, slug in targets:
            url = f"{APP_URL}/{slug}"
            first = visit(page, url)
            if first[0] == "private":
                note("warning", "The app asks visitors to sign in (it's private), so keep-warm visits can't open it. "
                                "Nothing else to do: friends wake it by clicking the button. To use keep-warm, set "
                                "the app's sharing to public in Streamlit Cloud, or turn this workflow off.")
                browser.close()
                return 0
            if not rows and first[0] != "ready":
                # The first page didn't open at all: don't spend 2 minutes on every other page too.
                title_text = ""
                try:
                    title_text = page.title()
                except PlaywrightError:
                    pass
                note("error", f"Couldn't open the app's first page ({first[0]} after {first[1]:.0f} s). The browser "
                              f"ended up at {page.url} (page title: {title_text or '-'}). If that's a sign-in page, "
                              "the app is private: set it to public in Streamlit Cloud or turn this workflow off.")
                browser.close()
                return 1
            second = visit(page, url) if first[0] == "ready" else ("skipped", None, [])
            rows.append((title, first, second))
            print(f"  {title:28} first {first[1]:5.1f} s ({first[0]}), again "
                  + (f"{second[1]:5.1f} s" if second[1] is not None else "-"), flush=True)
            if first[0] != "ready" or first[2] or second[2]:
                broken.append((title, first[0], first[2] or second[2]))
        browser.close()

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["## Keep-warm visit", "", f"Every page of {APP_URL}, in a headless phone-size browser. "
                 "First = right after the visit before it (cached unless new data arrived); again = straight after.",
                 "", "| Page | First (s) | Again (s) | Status |", "|---|---:|---:|---|"]
        for title, first, second in rows:
            lines.append(f"| {title} | {first[1]:.1f} | {'' if second[1] is None else f'{second[1]:.1f}'} | "
                         f"{first[0]}{' (shows an error)' if first[2] or second[2] else ''} |")
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    for title, status, problems in broken:
        note("warning", f"{title}: {status}" + (f"; the page shows: {problems[0]}" if problems else ""))
    ready = sum(1 for _, first, _ in rows if first[0] == "ready")
    print(f"{ready} of {len(rows)} pages opened.")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
