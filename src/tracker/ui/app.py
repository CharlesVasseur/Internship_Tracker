import streamlit as st

from tracker.ui.firms_page import render_firms_page
from tracker.ui.next_actions_page import render_next_actions_page


st.set_page_config(
    page_title="Internship Tracker",
    layout="wide",
)

firms_page = st.Page(
    render_firms_page,
    title="Firms",
    icon=":material/business:",
)

next_actions_page = st.Page(
    render_next_actions_page,
    title="Next actions",
    icon=":material/checklist:",
)

navigation = st.navigation([next_actions_page, firms_page])
navigation.run()
