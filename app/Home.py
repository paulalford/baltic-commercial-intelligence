"""Milestone 1 landing page; no business data or analytics are loaded."""

import streamlit as st

st.set_page_config(page_title="Baltic Sales Intelligence", page_icon="📊", layout="wide")
st.title("Baltic Sales Intelligence")
st.subheader("Fictional synthetic commercial analytics case study")
st.info(
    "Baltic Trade Supplies UAB, its people, customers, suppliers, transactions and "
    "commercial performance are entirely fictional and will be generated synthetically."
)
st.write(
    "Milestone 1 establishes the repository, configuration, logging and command-line "
    "foundation. No business data, KPIs, forecasts or management findings are available yet."
)
