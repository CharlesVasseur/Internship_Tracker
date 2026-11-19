"""Read-only Firms page."""

from pathlib import Path
import sqlite3

import streamlit as st

from tracker.db import connect
from tracker.queries import get_db_path, get_filter_options, list_firms


def render_firms_page() -> None:
    """Render the database-backed firms table."""
    db_path = get_db_path()
    if not Path(db_path).is_file():
        st.error(
            f"Database not found: {db_path}\n\n"
            "Import the workbook with:\n"
            f"python scripts/import_excel.py --xlsx <xlsx> --db {db_path} --profile charles"
        )
        st.stop()

    connection = None
    try:
        connection = connect(db_path)
        options = get_filter_options(connection)

        countries = st.sidebar.multiselect(
            "Country",
            options["countries"],
        )
        priorities = st.sidebar.multiselect(
            "Priority",
            options["priorities"],
        )
        stages = st.sidebar.multiselect(
            "Stage",
            options["stages"],
        )
        search = st.sidebar.text_input("Name or employer key")

        all_firms = list_firms(connection)
        shown_firms = list_firms(
            connection,
            countries=countries,
            priorities=priorities,
            stages=stages,
            search=search,
        )

        employer_keys = {
            firm["employer_key"]
            for firm in shown_firms
            if firm["employer_key"] is not None
        }

        metric_columns = st.columns(3)
        metric_columns[0].metric("Total firms", len(all_firms))
        metric_columns[1].metric("Firms shown", len(shown_firms))
        metric_columns[2].metric("Employers shown", len(employer_keys))

        if not shown_firms:
            st.info("No firm matches these filters.")
            return

        rows = [
            {
                "Name": firm["name"],
                "Country": firm["country"],
                "City": firm["city"],
                "Priority": firm["priority"],
                "Stage": firm["stage"],
                "Next action": firm["next_action_date"],
                "Category": firm["category"],
                "Actuarial depth": firm["actuarial_depth"],
                "Opening window": firm["opening_window"],
                "Last verified": firm["last_verified"],
                "Link": firm["careers_url"],
            }
            for firm in shown_firms
        ]
        st.dataframe(
            rows,
            hide_index=True,
            column_order=[
                "Name",
                "Country",
                "City",
                "Priority",
                "Stage",
                "Next action",
                "Category",
                "Actuarial depth",
                "Opening window",
                "Last verified",
                "Link",
            ],
            column_config={
                "Link": st.column_config.LinkColumn(
                    "Link",
                    display_text="Open",
                ),
            },
        )
    except sqlite3.Error as error:
        st.error(f"Database error: {error}")
    finally:
        if connection is not None:
            connection.close()
