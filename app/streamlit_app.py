"""
app/streamlit_app.py

The National Fantasy League history dashboard (Phase 4, first version).

Run it on your computer from the project root:
    streamlit run app/streamlit_app.py
It opens in your browser at http://localhost:8501. Stop it with Ctrl+C in the terminal.
"""

import streamlit as st

st.set_page_config(page_title="National Fantasy League", page_icon=":material/sports_football:", layout="wide")

pages = st.navigation(
    {
        "League history": [
            st.Page("pages/home.py", title="Home", icon=":material/home:", default=True),
            st.Page("pages/standings.py", title="Standings", icon=":material/format_list_numbered:"),
            st.Page("pages/head_to_head.py", title="Head-to-head", icon=":material/swords:"),
            st.Page("pages/champions.py", title="Champions & playoffs", icon=":material/emoji_events:"),
            st.Page("pages/record_book.py", title="Record book", icon=":material/menu_book:"),
            st.Page("pages/franchises.py", title="Franchises", icon=":material/shield:"),
        ]
    }
)
pages.run()
