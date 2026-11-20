"""Read-only Firms page."""

from pathlib import Path
from datetime import date, datetime
import hashlib
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
    list_checks,
    list_firms,
    list_opportunities,
    list_other_offices,
)
from tracker.service import (
    add_application,
    add_note,
    save_application,
    record_manual_check,
    update_firm_link,
)


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
                "Link": firm["careers_url"],
                "Country": firm["country"],
                "City": firm["city"],
                "Priority": firm["priority"],
                "Latest stage": firm["stage"],
                "Applications": firm["n_applications"],
                "Next action": firm["next_action_date"],
                "Last checked": (
                    firm["last_successful_check"][:10]
                    if firm["last_successful_check"] is not None
                    else "never"
                ),
                "Category": firm["category"],
                "Opening window": firm["opening_window"],
                "Last verified": firm["last_verified"],
            }
            for firm in shown_firms
        ]
        filter_state = (countries, priorities, stages, search)
        filter_hash = hashlib.sha1(
            repr(filter_state).encode()
        ).hexdigest()[:8]
        table_key = f"firms_table_{filter_hash}"
        table_event = st.dataframe(
            rows,
            hide_index=True,
            column_order=[
                "Name",
                "Link",
                "Country",
                "City",
                "Priority",
                "Latest stage",
                "Applications",
                "Next action",
                "Last checked",
                "Category",
                "Opening window",
                "Last verified",
            ],
            column_config={
                "Link": st.column_config.LinkColumn(
                    "Link",
                    display_text="Open",
                ),
            },
            on_select="rerun",
            selection_mode="single-row",
            key=table_key,
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

            with st.expander("Save a real careers link"):
                current_url = (
                    detail["careers_url"]
                    if detail["source_type"] != "search_link"
                    else ""
                )
                with st.form(
                    key=f"link_{selected_firm['firm_id']}",
                    clear_on_submit=False,
                    border=True,
                ):
                    careers_url = st.text_input(
                        "Careers page or posting URL",
                        value=current_url or "",
                    )
                    link_type = st.selectbox(
                        "Link type",
                        ("careers_page", "direct_posting"),
                    )
                    apply_to_offices = st.checkbox(
                        "Also apply to other offices of this employer "
                        "that still have a search link",
                        value=False,
                    )
                    link_submitted = st.form_submit_button("Save link")

                if link_submitted:
                    try:
                        update_firm_link(
                            connection,
                            selected_firm["firm_id"],
                            careers_url,
                            link_type,
                            apply_to_other_offices=apply_to_offices,
                        )
                    except (ValueError, LookupError, sqlite3.Error) as error:
                        st.error(str(error))
                    else:
                        st.rerun()

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
                            "Stage of this application",
                            APPLICATION_STAGES,
                            index=stage_index,
                        )
                        change_date = st.date_input(
                            "Date of the change",
                            value=date.today(),
                            format="YYYY-MM-DD",
                            help=(
                                "Written in the log when you change the stage. "
                                "Also used as the submitted date the first time "
                                "you set Applied."
                            ),
                        )
                        next_action = st.text_input(
                            "Next action (what to do)",
                            value=application["next_action"] or "",
                        )
                        stored_next_action_date = application["next_action_date"]
                        picker_value = None
                        if stored_next_action_date is not None:
                            try:
                                picker_value = date.fromisoformat(
                                    stored_next_action_date
                                )
                            except (TypeError, ValueError):
                                st.warning(
                                    f"Stored next action date "
                                    f"'{stored_next_action_date}' is not a valid date; "
                                    "saving here replaces it."
                                )
                        next_action_date = st.date_input(
                            "Next action date",
                            value=picker_value,
                            format="YYYY-MM-DD",
                        )
                        clear_next_action_date = st.checkbox(
                            "Clear the next action date",
                            value=False,
                            help="Tick and press Save to remove the date.",
                        )
                        save_submitted = st.form_submit_button("Save application")

                    if save_submitted:
                        try:
                            save_application(
                                connection,
                                application_id,
                                stage=new_stage,
                                on_date=change_date,
                                next_action=next_action,
                                next_action_date=(
                                    None
                                    if clear_next_action_date
                                    else next_action_date
                                ),
                                notes=application["notes"],
                            )
                        except (ValueError, LookupError, sqlite3.Error) as error:
                            st.error(str(error))
                        else:
                            st.rerun()

                    if application["notes"] and application["notes"].strip():
                        st.markdown("**Earlier notes**")
                        st.text(application["notes"])
                        st.caption(
                            "Read-only. Write new notes as log entries below."
                        )

                    activities = list_activities(
                        connection,
                        application_id,
                    )
                    st.markdown("**Activity log**")
                    st.caption(
                        "Dated history. Every stage change is added automatically. "
                        "Entries cannot be edited."
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
                        note_text = st.text_input("New log entry")
                        note_submitted = st.form_submit_button("Add to log")

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

            st.divider()
            st.subheader("Add another application for this firm")
            st.caption(
                "Only for a second role or season at the same firm. "
                "The applications above are edited in their own boxes."
            )
            with st.form(
                key=f"add_app_{selected_firm['firm_id']}",
                clear_on_submit=False,
                border=True,
            ):
                new_application_stage = st.selectbox(
                    "Stage of the new application",
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

            st.subheader("Monitoring")
            last_successful_check = detail["last_successful_check"]
            last_check_status = detail["last_check_status"] or "-"
            st.write(
                "Last successful check: "
                f"{last_successful_check or 'never'} "
                f"(status: {last_check_status})"
            )
            with st.form(
                key=f"manual_check_{selected_firm['firm_id']}",
                clear_on_submit=False,
                border=True,
            ):
                manual_check_submitted = st.form_submit_button(
                    "I checked this page today"
                )

            if manual_check_submitted:
                try:
                    record_manual_check(
                        connection,
                        selected_firm["firm_id"],
                        datetime.now().replace(microsecond=0).isoformat(),
                    )
                except (ValueError, LookupError, sqlite3.Error) as error:
                    st.error(str(error))
                else:
                    st.rerun()

            st.caption(
                "Records that YOU looked at the saved link. "
                "It does not change any opportunity."
            )
            st.markdown("**Check log**")
            checks = list_checks(connection, selected_firm["firm_id"])
            if checks:
                check_rows = [
                    {
                        "Finished": check["finished_at"],
                        "Result": check["result"],
                        "Results": check["n_results"],
                        "HTTP": check["http_status"],
                        "Error": check["error"],
                        "Evidence": check["evidence_snippet"],
                    }
                    for check in checks
                ]
                st.dataframe(
                    check_rows,
                    hide_index=True,
                    column_order=[
                        "Finished",
                        "Result",
                        "Results",
                        "HTTP",
                        "Error",
                        "Evidence",
                    ],
                )
            else:
                st.caption("No check yet.")

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
                        "State": opportunity["state"],
                        "First seen": opportunity["first_seen"],
                        "Last seen": opportunity["last_seen"],
                        "Missing count": opportunity["missing_count"],
                        "Link": opportunity["url"],
                    }
                    for opportunity in opportunities
                ]
                st.dataframe(
                    opportunity_rows,
                    hide_index=True,
                    column_order=[
                        "Title",
                        "Location",
                        "State",
                        "First seen",
                        "Last seen",
                        "Missing count",
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
                st.info(
                    "No opportunities tracked yet. "
                    "Monitoring is manual for now: use the saved link."
                )
    except sqlite3.Error as error:
        st.error(f"Database error: {error}")
    finally:
        if connection is not None:
            connection.close()
