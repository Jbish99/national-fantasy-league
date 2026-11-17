"""
app/streamlit_app.py

The National Fantasy League history dashboard.

Run it on your computer from the project root:
    streamlit run app/streamlit_app.py
It opens in your browser at http://localhost:8501. Stop it with Ctrl+C in the terminal.
"""

import importlib
import mimetypes
from pathlib import Path

import streamlit as st

import lib
import story

# A running app re-reads the page files after a push, but keeps the helper modules it loaded first in memory.
# A page that imports something new from lib.py then fails with ImportError until the app restarts
# (seen on Streamlit Cloud 2026-11-16). So: reload a helper whenever its file on disk is newer. lib goes first,
# and story (which imports from lib) is reloaded whenever lib is.
_reloaded = False
for _module in (lib, story):
    if _reloaded or getattr(_module, "LOADED_MTIME", None) != Path(_module.__file__).stat().st_mtime:
        importlib.reload(_module)
        _reloaded = True

from lib import PROJECT_ROOT, style  # noqa: E402  (after the reload, so these are the current versions)

# Serve the end-zone banners and the font with the right file type (older Pythons don't know these two).
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("font/woff2", ".woff2")

st.set_page_config(page_title="National Fantasy League",
                   page_icon=str(PROJECT_ROOT / "app" / "static" / "league" / "LEAGUE" / "league_logo.png"),
                   layout="wide")
style()

pages = st.navigation(
    {
        "League history": [
            st.Page("views/home.py", title="Home", icon=":material/home:", default=True),
            st.Page("views/standings.py", title="Standings", icon=":material/format_list_numbered:"),
            # url_path: the page addresses lib.box_url() / lib.player_url() link to
            st.Page("views/box_score.py", title="Box scores", icon=":material/scoreboard:", url_path="boxscore"),
            st.Page("views/head_to_head.py", title="Head-to-head", icon=":material/swords:"),
            st.Page("views/champions.py", title="Champions & playoffs", icon=":material/emoji_events:"),
            st.Page("views/record_book.py", title="Record book", icon=":material/menu_book:"),
            st.Page("views/franchises.py", title="Franchises", icon=":material/shield:"),
            st.Page("views/player.py", title="Players", icon=":material/person:", url_path="player"),
            # url_path "rankings": the address lib.rankings_url() links to (Phase 12)
            st.Page("views/rankings.py", title="Player rankings", icon=":material/leaderboard:", url_path="rankings"),
        ],
        "Getting better": [
            st.Page("views/lineups.py", title="Lineups", icon=":material/event_seat:"),
            st.Page("views/luck.py", title="Luck", icon=":material/casino:"),
            # url_path "trades": the address lib.trade_url() links to
            st.Page("views/trades.py", title="Trade analyzer", icon=":material/swap_horiz:", url_path="trades"),
            st.Page("views/transactions.py", title="Waivers & FAAB", icon=":material/person_add:"),
            st.Page("views/draft.py", title="Draft", icon=":material/format_list_bulleted:"),
            st.Page("views/positions.py", title="Positions", icon=":material/grid_view:"),
            st.Page("views/identities.py", title="Team identities", icon=":material/fingerprint:", url_path="identities"),
        ],
    }
)
pages.run()
