"""
app/streamlit_app.py

The National Fantasy League history dashboard.

Run it on your computer from the project root:
    streamlit run app/streamlit_app.py
It opens in your browser at http://localhost:8501. Stop it with Ctrl+C in the terminal.
"""

import mimetypes

import streamlit as st

from lib import PROJECT_ROOT, style

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
            st.Page("views/head_to_head.py", title="Head-to-head", icon=":material/swords:"),
            st.Page("views/champions.py", title="Champions & playoffs", icon=":material/emoji_events:"),
            st.Page("views/record_book.py", title="Record book", icon=":material/menu_book:"),
            st.Page("views/franchises.py", title="Franchises", icon=":material/shield:"),
        ],
        "Getting better": [
            st.Page("views/lineups.py", title="Lineups", icon=":material/event_seat:"),
            st.Page("views/luck.py", title="Luck", icon=":material/casino:"),
            st.Page("views/transactions.py", title="Trades & waivers", icon=":material/swap_horiz:"),
            st.Page("views/draft.py", title="Draft", icon=":material/format_list_bulleted:"),
            st.Page("views/positions.py", title="Positions", icon=":material/grid_view:"),
        ],
    }
)
pages.run()
