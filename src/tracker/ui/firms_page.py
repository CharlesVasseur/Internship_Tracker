"""Read-only Firms page."""

from pathlib import Path
from datetime import date
import sqlite3

import streamlit as st

from tracker.db import connect
from tracker.models import APPLICATION_STAGES
from tracker.queries import (
    get_db_path,
    get_firm,
    get_filter_options,
    list_activities,
    list_applications,
    list_firms,
    list_opportunities,
    list_other_offices,
)
from tracker.service import add_application, add_note, save_application


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
                "Latest stage": firm["stage"],
                "Applications": firm["n_applications"],
                "Next action": firm["next_action_date"],
                "Category": firm["category"],
                "Actuarial depth": firm["actuarial_depth"],
                "Opening window": firm["opening_window"],
                "Last verified": firm["last_verified"],
                "Link": firm["careers_url"],
            }
            for firm in shown_firms
        ]
        table_event = st.dataframe(
            rows,
            hide_index=True,
            column_order=[
                "Name",
                "Country",
                "City",
                "Priority",
                "Latest stage",
                "Applications",
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
            on_select="rerun",
            selection_mode="single-row",
            key="firms_table",
        )

        selected_rows = table_event.selection.rows
        if selected_rows:
            position = selected_rows[0]
            if position < 0 or position >= len(shown_firms):
                return

            selected_firm = shown_firms[position]
            detail = get_firm(connection, selected_firm["firm_id"])
            if detail is None:
                return

            st.divider()
            st.header(
                f"{detail['name']} - {detail['city'] or '-'}, {detail['country']}"
            )

            facts = [
                ("Priority", detail["priority"]),
                ("Category", detail["category"]),
                ("Actuarial depth", detail["actuarial_depth"]),
                ("Role types", detail["role_types"]),
                ("Eligibility", detail["eligibility"]),
                ("Internship type", detail["internship_type"]),
                ("Language fit", detail["language_fit"]),
                ("Work authorisation", detail["work_authorisation"]),
                ("Opening window", detail["opening_window"]),
                ("Last verified", detail["last_verified"]),
                ("Notes", detail["notes"]),
            ]
            fact_columns = st.columns(3)
            for index, (label, value) in enumerate(facts):
                fact_columns[index % 3].markdown(
                    f"**{label}:** {value if value not in (None, '') else '-'}"
                )

            if detail["careers_url"]:
                st.markdown(f"[Open saved link]({detail['careers_url']})")
            if detail["source_type"] == "search_link":
                st.caption("This is a search link, not a careers page.")

            st.subheader("Other offices of this employer")
            other_offices = list_other_offices(
                connection,
                selected_firm["firm_id"],
            )
            if other_offices:
                for office in other_offices:
                    st.write(
                        f"{office['country'] or '-'} - "
                        f"{office['city'] or '-'}"
                    )
            else:
                st.caption("No other office.")

            st.subheader("Applications")
            applications = list_applications(
                connection,
                selected_firm["firm_id"],
            )
            if not applications:
                st.info("No application yet.")

            for application in applications:
                application_id = application["id"]
                with st.container(border=True):
                    st.markdown(
                        f"**Application #{application_id} - "
                        f"{application['stage']}**"
                    )
                    with st.form(
                        key=f"save_app_{application_id}",
                        clear_on_submit=False,
                        border=True,
                    ):
                        stage_index = APPLICATION_STAGES.index(application["stage"])
                        new_stage = st.selectbox(
                            "Stage",
                            APPLICATION_STAGES,
                            index=stage_index,
                        )
                        change_date = st.date_input(
                            "Date of the change",
                            value=date.today(),
                            format="YYYY-MM-DD",
                        )
                        next_action = st.text_input(
                            "Next action",
                            value=application["next_action"] or "",
                        )
                        next_action_date = st.text_input(
                            "Next action date (YYYY-MM-DD)",
                            value=application["next_action_date"] or "",
                        )
                        notes = st.text_area(
                            "Notes",
                            value=application["notes"] or "",
                        )
                        save_submitted = st.form_submit_button("Save")

                    if save_submitted:
                        try:
                            save_application(
                                connection,
                                application_id,
                                stage=new_stage,
                                on_date=change_date,
                                next_action=next_action,
                                next_action_date=next_action_date,
                                notes=notes,
                            )
                        except (ValueError, LookupError, sqlite3.Error) as error:
                            st.error(str(error))
                        else:
                            st.rerun()

                    activities = list_activities(
                        connection,
                        application_id,
                    )
                    if activities:
                        activity_rows = [
                            {
                                "Date": activity["date"],
                                "Type": activity["type"],
                                "Notes": activity["notes"],
                            }
                            for activity in activities
                        ]
                        st.dataframe(
                            activity_rows,
                            hide_index=True,
                            column_order=["Date", "Type", "Notes"],
                        )
                    else:
                        st.caption("No activity yet.")

                    with st.form(
                        key=f"note_{application_id}",
                        clear_on_submit=True,
                        border=True,
                    ):
                        note_text = st.text_input("Add a note to the log")
                        note_submitted = st.form_submit_button("Add note")

                    if note_submitted:
                        try:
                            add_note(
                                connection,
                                application_id,
                                note_text,
                                on_date=date.today(),
                            )
                        except (ValueError, LookupError, sqlite3.Error) as error:
                            st.error(str(error))
                        else:
                            st.rerun()

            with st.form(
                key=f"add_app_{selected_firm['firm_id']}",
                clear_on_submit=False,
                border=True,
            ):
                new_application_stage = st.selectbox(
                    "Stage",
                    APPLICATION_STAGES,
                    index=0,
                )
                add_submitted = st.form_submit_button("Add application")

            if add_submitted:
                try:
                    add_application(
                        connection,
                        selected_firm["firm_id"],
                        stage=new_application_stage,
                        on_date=date.today(),
                    )
                except (ValueError, LookupError, sqlite3.Error) as error:
                    st.error(str(error))
                else:
                    st.rerun()

            st.subheader("Opportunities")
            opportunities = list_opportunities(
                connection,
                selected_firm["firm_id"],
            )
            if opportunities:
                opportunity_rows = [
                    {
                        "Title": opportunity["title"],
                        "Location": opportunity["location"],
                        "URL": opportunity["url"],
                        "State": opportunity["state"],
                        "First seen": opportunity["first_seen"],
                        "Last seen": opportunity["last_seen"],
                        "Internship": opportunity["is_internship"],
                    }
                    for opportunity in opportunities
                ]
                st.dataframe(opportunity_rows, hide_index=True)
            else:
                st.info(
                    "No opportunities tracked yet. "
                    "Monitoring is manual for now: use the saved link."
                )
    except sqlite3.Error as error:
        st.error(f"Database error: {error}")
    finally:
        if connection is not None:
            connection.close()
