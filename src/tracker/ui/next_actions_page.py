"""Next-actions dashboard."""

from datetime import date
from pathlib import Path
import sqlite3

import streamlit as st

from tracker.db import connect, init_db
from tracker.queries import (
    get_db_path,
    list_next_actions,
    list_opportunity_deadlines,
)


def _next_action_rows(items: list[dict], include_days_late: bool = False, today: date | None = None) -> list[dict]:
    """Convert query items into the dataframe shape used by the page."""
    rows = []
    for item in items:
        row = {
            "Firm": item["firm_name"],
            "Country": item["country"],
            "City": item["city"],
            "Stage": item["stage"],
            "Next action": item["next_action"] or "",
            "Date": item["next_action_date"] or "",
        }
        if include_days_late:
            action_date = date.fromisoformat(item["next_action_date"])
            row["Days late"] = (today - action_date).days
        rows.append(row)
    return rows


def _opportunity_deadline_rows(
    items: list[dict],
    include_days_late: bool = False,
    today: date | None = None,
) -> list[dict]:
    """Convert opportunity deadlines into the dataframe shape used by the page."""
    rows = []
    for item in items:
        row = {
            "Firm": item["firm_name"],
            "Country": item["country"],
            "City": item["city"],
            "Title": item["title"],
            "Deadline": item["deadline"],
            "State": item["state"],
            "Link": item["url"],
        }
        if include_days_late:
            deadline = date.fromisoformat(item["deadline"])
            row["Days late"] = (today - deadline).days
        rows.append(row)
    return rows


def render_next_actions_page() -> None:
    """Render next actions grouped by due state."""
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
        today = date.today()
        actions = list_next_actions(connection, today.isoformat())
        opportunities = list_opportunity_deadlines(
            connection,
            today.isoformat(),
        )

        metric_columns = st.columns(5)
        metric_columns[0].metric("Overdue", len(actions["overdue"]))
        metric_columns[1].metric("Today", len(actions["today"]))
        metric_columns[2].metric("Next 7 days", len(actions["upcoming"]))
        metric_columns[3].metric("No date", len(actions["no_date"]))
        metric_columns[4].metric(
            "Deadlines (14 days)",
            len(opportunities["soon"]),
        )

        sections = (
            ("Overdue", "overdue", True),
            ("Today", "today", False),
            ("Next 7 days", "upcoming", False),
            ("No date", "no_date", False),
        )
        for title, key, include_days_late in sections:
            st.subheader(title)
            items = actions[key]
            if items:
                st.dataframe(
                    _next_action_rows(
                        items,
                        include_days_late=include_days_late,
                        today=today,
                    ),
                    hide_index=True,
                )
            else:
                st.caption("Nothing here.")

        if actions["unreadable"]:
            st.warning("Some stored next action dates are not valid ISO dates.")
            st.subheader("Unreadable")
            st.dataframe(
                _next_action_rows(actions["unreadable"]),
                hide_index=True,
            )

        st.subheader("Opportunity deadlines")
        if opportunities["soon"]:
            st.dataframe(
                _opportunity_deadline_rows(opportunities["soon"]),
                hide_index=True,
                column_order=[
                    "Firm",
                    "Country",
                    "City",
                    "Title",
                    "Deadline",
                    "State",
                    "Link",
                ],
                column_config={
                    "Link": st.column_config.LinkColumn(
                        "Link",
                        display_text="Open",
                    ),
                },
            )
        else:
            st.caption("Nothing here.")

        st.subheader("Deadline passed (still open)")
        if opportunities["passed"]:
            st.dataframe(
                _opportunity_deadline_rows(
                    opportunities["passed"],
                    include_days_late=True,
                    today=today,
                ),
                hide_index=True,
                column_order=[
                    "Firm",
                    "Country",
                    "City",
                    "Title",
                    "Deadline",
                    "State",
                    "Link",
                    "Days late",
                ],
                column_config={
                    "Link": st.column_config.LinkColumn(
                        "Link",
                        display_text="Open",
                    ),
                },
            )
        else:
            st.caption("Nothing here.")

        if opportunities["unreadable"]:
            st.warning("Some opportunity deadlines are not valid dates.")
            st.subheader("Unreadable opportunity deadlines")
            st.dataframe(
                _opportunity_deadline_rows(opportunities["unreadable"]),
                hide_index=True,
                column_order=[
                    "Firm",
                    "Country",
                    "City",
                    "Title",
                    "Deadline",
                    "State",
                    "Link",
                ],
                column_config={
                    "Link": st.column_config.LinkColumn(
                        "Link",
                        display_text="Open",
                    ),
                },
            )

        if (
            all(not items for items in actions.values())
            and all(not items for items in opportunities.values())
        ):
            st.info("No next actions planned.")
    except sqlite3.Error as error:
        st.error(f"Database error: {error}")
    finally:
        if connection is not None:
            connection.close()
