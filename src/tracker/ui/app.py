import streamlit as st

from tracker.ui.firms_page import render_firms_page


st.set_page_config(
    page_title="Internship Tracker",
    layout="wide",
)

firms_page = st.Page(
    render_firms_page,
    title="Firms",
    icon=":material/business:",
)

navigation = st.navigation([firms_page])
navigation.run()
