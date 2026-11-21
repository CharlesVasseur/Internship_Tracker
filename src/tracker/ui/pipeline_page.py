"""Read-only application pipeline page."""

from pathlib import Path
import sqlite3

import streamlit as st

from tracker.db import connect, init_db
from tracker.models import APPLICATION_STAGES
from tracker.queries import (
    count_by_stage,
    get_db_path,
    list_pipeline,
)


def render_pipeline_page() -> None:
    """Render stage counts and applications in selected stages."""
    db_path = get_db_path()
    if not Path(db_path).is_file():
        st.error(
            f"Database not found: {db_path}\n\n"
            "Import the workbook with:\n"
            f"python scripts/import_excel.py --xlsx <xlsx> --db <db_path>"
        )
        st.stop()

    try:
        init_db(db_path)
    except (sqlite3.Error, RuntimeError) as error:
        st.error(f"Database problem: {error}")
        st.stop()

    connection = None
    try:
        connection = connect(db_path)

        counts = dict(count_by_stage(connection))
        for row_index in range(2):
            metric_columns = st.columns(4)
            for column_index in range(4):
                stage_index = row_index * 4 + column_index
                stage = APPLICATION_STAGES[stage_index]
                metric_columns[column_index].metric(
                    stage,
                    counts[stage],
                )

        selected_stages = st.multiselect(
            "Stages shown",
            APPLICATION_STAGES,
            default=list(APPLICATION_STAGES[1:7]),
        )
        applications = list_pipeline(connection, selected_stages)

        if not applications:
            st.info("No application in the selected stages.")
            return

        rows = [
            {
                "Firm": application["firm_name"],
                "Country": application["country"],
                "City": application["city"],
                "Priority": application["priority"],
                "Stage": application["stage"],
                "Next action": application["next_action"],
                "Next action date": application["next_action_date"],
                "Submitted": application["submitted_date"],
            }
            for application in applications
        ]
        st.dataframe(
            rows,
            hide_index=True,
            column_order=[
                "Firm",
                "Country",
                "City",
                "Priority",
                "Stage",
                "Next action",
                "Next action date",
                "Submitted",
            ],
        )
    except sqlite3.Error as error:
        st.error(f"Database error: {error}")
    finally:
        if connection is not None:
            connection.close()
