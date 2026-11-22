"""Read-only Firms page."""

from pathlib import Path
from datetime import date, datetime
import hashlib
import os
import sqlite3

import streamlit as st

from tracker.db import connect, init_db
from tracker.adapters.paged_cards import ADAPTER_NAME
from tracker.models import APPLICATION_STAGES, OPPORTUNITY_STATES
from tracker.monitor import (
    MIN_MINUTES_BETWEEN_CHECKS,
    load_monitoring_config,
    run_check,
)
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
    add_manual_opportunity,
    delete_manual_opportunity,
    add_note,
    save_application,
    set_application_opportunity,
    set_opportunity_state,
    update_manual_opportunity,
    record_manual_check,
    set_firm_monitoring,
    disable_firm_monitoring,
    update_firm_link,
)


def render_firms_page() -> None:
    """Render the database-backed firms table."""
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
            opportunities = list_opportunities(
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

                    opportunity_options = ["None"] + [
                        (
                            f"{opportunity['title']}"
                            f"{' (deadline ' + opportunity['deadline'] + ')' if opportunity['deadline'] else ''}"
                        )
                        for opportunity in opportunities
                    ]
                    opportunity_ids = [None] + [
                        opportunity["id"] for opportunity in opportunities
                    ]
                    opportunity_by_label = {
                        label: opportunity_id
                        for label, opportunity_id in zip(
                            opportunity_options,
                            opportunity_ids,
                        )
                    }
                    current_option = 0
                    if application["opportunity_id"] is not None:
                        try:
                            current_option = opportunity_ids.index(
                                application["opportunity_id"]
                            )
                        except ValueError:
                            current_option = 0
                    with st.form(
                        key=f"link_opp_{application_id}",
                        clear_on_submit=False,
                        border=True,
                    ):
                        linked_label = st.selectbox(
                            "Linked opportunity",
                            opportunity_options,
                            index=current_option,
                        )
                        link_submitted = st.form_submit_button("Save link")

                    if link_submitted:
                        opportunity_id = (
                            None
                            if linked_label == "None"
                            else opportunity_by_label[linked_label]
                        )
                        try:
                            set_application_opportunity(
                                connection,
                                application_id,
                                opportunity_id,
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
            firm_id = selected_firm["firm_id"]
            check_msg_key = f"check_msg_{firm_id}"
            check_message = st.session_state.pop(check_msg_key, None)
            if check_message is not None:
                kind, text = check_message
                if kind == "success":
                    st.success(text)
                else:
                    st.error(text)

            last_successful_check = detail["last_successful_check"]
            last_check_status = detail["last_check_status"] or "-"
            st.write(
                "Last successful check: "
                f"{last_successful_check or 'never'} "
                f"(status: {last_check_status})"
            )
            with st.form(
                key=f"manual_check_{firm_id}",
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
                        firm_id,
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

            if detail["monitoring"] != "automated":
                with st.expander("Enable automatic checks (advanced)"):
                    st.warning(
                        "Only for sites whose terms of use you have READ and that "
                        "do not forbid automated access. A check makes about 8 "
                        "requests, one per second, only when you click Check now."
                    )
                    with st.form(
                        key=f"enable_monitoring_{firm_id}",
                        clear_on_submit=False,
                        border=True,
                    ):
                        prefilled_list_url = (
                            detail["careers_url"]
                            if detail["source_type"] != "search_link"
                            else ""
                        )
                        list_url = st.text_input(
                            "List page address",
                            value=prefilled_list_url or "",
                        )
                        api_url_template = st.text_input(
                            "API address template (must contain {page})"
                        )
                        terms_url = st.text_input(
                            "Terms of use page you read (address)"
                        )
                        terms_choices = {
                            "Silent: no mention of automated access": "silent",
                            "Permission granted in writing": "permission_granted",
                            "Forbids automated access": "forbids",
                        }
                        terms_choice = st.selectbox(
                            "What did the terms say?",
                            list(terms_choices),
                        )
                        terms_reviewed_on = st.date_input(
                            "Date you read them",
                            value=date.today(),
                            format="YYYY-MM-DD",
                        )
                        terms_note = st.text_area(
                            "Your note (what you read, in your own words)"
                        )
                        enable_submitted = st.form_submit_button(
                            "Enable automatic checks"
                        )

                    if enable_submitted:
                        try:
                            set_firm_monitoring(
                                connection,
                                firm_id,
                                adapter=ADAPTER_NAME,
                                list_url=list_url,
                                api_url_template=api_url_template,
                                terms_url=terms_url,
                                terms_reading=terms_choices[terms_choice],
                                terms_reviewed_on=terms_reviewed_on,
                                terms_note=terms_note or None,
                            )
                        except (ValueError, LookupError, sqlite3.Error) as error:
                            st.error(str(error))
                        else:
                            st.rerun()
            else:
                try:
                    monitoring_config = load_monitoring_config(
                        detail["source_config"]
                    )
                except ValueError as error:
                    st.warning(f"Monitoring configuration is unreadable: {error}")
                    with st.form(
                        key=f"disable_monitoring_{firm_id}",
                        clear_on_submit=False,
                        border=True,
                    ):
                        disable_submitted = st.form_submit_button(
                            "Disable automatic checks"
                        )
                    if disable_submitted:
                        try:
                            disable_firm_monitoring(connection, firm_id)
                        except (LookupError, sqlite3.Error) as error:
                            st.error(str(error))
                        else:
                            st.rerun()
                else:
                    terms = monitoring_config["terms"]
                    terms_url = terms["url"]
                    st.markdown(
                        "Automatic checks: enabled. "
                        f"Terms reviewed on {terms['reviewed_on']} "
                        f"({terms['reading']}). "
                        f"[Terms of use]({terms_url})"
                    )
                    st.caption(
                        f"Cooldown: at least {MIN_MINUTES_BETWEEN_CHECKS} minutes "
                        "between automatic checks. A check makes about 8 requests, "
                        "one per second."
                    )
                    with st.form(
                        key=f"run_check_{firm_id}",
                        clear_on_submit=False,
                        border=True,
                    ):
                        run_submitted = st.form_submit_button(
                            "Check now (automatic)"
                        )

                    if not os.environ.get("TRACKER_CONTACT", "").strip():
                        st.caption(
                            "Set the TRACKER_CONTACT environment variable before "
                            "starting the app to run automatic checks."
                        )

                    if run_submitted:
                        try:
                            with st.spinner("Checking..."):
                                check_result = run_check(connection, firm_id)
                        except (ValueError, LookupError, sqlite3.Error) as error:
                            st.error(str(error))
                        else:
                            newest_checks = list_checks(
                                connection,
                                firm_id,
                                limit=1,
                            )
                            newest = newest_checks[0] if newest_checks else None
                            status = check_result["status"]
                            if status in ("ok", "empty"):
                                text = (
                                    f"Check {status}: "
                                    f"{check_result['new']} new, "
                                    f"{check_result['changed']} changed, "
                                    f"{check_result['unchanged']} unchanged, "
                                    f"{check_result['missing']} missing, "
                                    f"{check_result['closed']} closed, "
                                    f"{check_result['reopened']} reopened "
                                    f"({check_result['requests']} requests)."
                                )
                                st.session_state[check_msg_key] = ("success", text)
                            elif status in ("failed", "blocked"):
                                error_text = (
                                    newest["error"]
                                    if newest is not None
                                    and newest["error"]
                                    else "unknown error"
                                )
                                text = (
                                    f"Check {status}: {error_text}. "
                                    "Nothing was changed in your opportunities."
                                )
                                st.session_state[check_msg_key] = ("error", text)
                            st.rerun()

                    with st.form(
                        key=f"disable_monitoring_{firm_id}",
                        clear_on_submit=False,
                        border=True,
                    ):
                        disable_submitted = st.form_submit_button(
                            "Disable automatic checks"
                        )
                    if disable_submitted:
                        try:
                            disable_firm_monitoring(connection, firm_id)
                        except (LookupError, sqlite3.Error) as error:
                            st.error(str(error))
                        else:
                            st.rerun()

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
            show_closed = st.checkbox(
                "Show dismissed and closed opportunities",
                value=False,
                key=f"show_closed_{selected_firm['firm_id']}",
            )
            shown_opportunities = (
                opportunities
                if show_closed
                else [
                    opportunity
                    for opportunity in opportunities
                    if opportunity["state"] not in ("Closed", "No match")
                ]
            )
            hidden_count = len(opportunities) - len(shown_opportunities)
            if hidden_count:
                st.caption(
                    f"{hidden_count} hidden (closed or no match)."
                )

            if shown_opportunities:
                opportunity_rows = [
                    {
                        "Title": opportunity["title"],
                        "Link": opportunity["url"],
                        "Location": opportunity["location"],
                        "Deadline": opportunity["deadline"],
                        "State": opportunity["state"],
                        "Source": (
                            "Manual"
                            if opportunity["is_manual"]
                            else "Automatic"
                        ),
                        "Last seen": opportunity["last_seen"],
                        "Missing count": opportunity["missing_count"],
                    }
                    for opportunity in shown_opportunities
                ]
                st.dataframe(
                    opportunity_rows,
                    hide_index=True,
                    column_order=[
                        "Title",
                        "Link",
                        "Location",
                        "Deadline",
                        "State",
                        "Source",
                        "Last seen",
                        "Missing count",
                    ],
                    column_config={
                        "Link": st.column_config.LinkColumn(
                            "Link",
                            display_text="Open",
                        ),
                    },
                )
            else:
                if opportunities:
                    st.info("No visible opportunities with this filter.")
                else:
                    st.info(
                        "No opportunities tracked yet. "
                        "Monitoring is manual for now: use the saved link."
                    )

            for opportunity in shown_opportunities:
                if opportunity["is_manual"]:
                    continue
                with st.expander(f"State: {opportunity['title']}"):
                    with st.form(
                        key=f"state_opp_{opportunity['id']}",
                        clear_on_submit=False,
                        border=True,
                    ):
                        new_state = st.selectbox(
                            "State",
                            OPPORTUNITY_STATES,
                            index=OPPORTUNITY_STATES.index(
                                opportunity["state"]
                            ),
                        )
                        state_submitted = st.form_submit_button("Set state")

                    if state_submitted:
                        try:
                            set_opportunity_state(
                                connection,
                                opportunity["id"],
                                new_state,
                            )
                        except (ValueError, LookupError, sqlite3.Error) as error:
                            st.error(str(error))
                        else:
                            st.rerun()

            with st.expander("Add an opportunity"):
                with st.form(
                    key=f"add_opp_{selected_firm['firm_id']}",
                    clear_on_submit=False,
                    border=True,
                ):
                    add_title = st.text_input("Title")
                    add_url = st.text_input("Link (https://...)")
                    add_location = st.text_input("Location")
                    add_deadline = st.date_input(
                        "Deadline",
                        value=None,
                        format="YYYY-MM-DD",
                        help="Leave empty if unknown.",
                    )
                    add_state = st.selectbox(
                        "State",
                        OPPORTUNITY_STATES,
                        index=OPPORTUNITY_STATES.index("Open"),
                    )
                    add_notes = st.text_area("Notes")
                    add_opportunity_submitted = st.form_submit_button(
                        "Add opportunity"
                    )

                if add_opportunity_submitted:
                    try:
                        add_manual_opportunity(
                            connection,
                            selected_firm["firm_id"],
                            add_title,
                            url=add_url,
                            location=add_location,
                            deadline=add_deadline,
                            notes=add_notes,
                            state=add_state,
                            on_date=date.today(),
                        )
                    except (ValueError, LookupError, sqlite3.Error) as error:
                        st.error(str(error))
                    else:
                        st.rerun()

            for opportunity in opportunities:
                if not opportunity["is_manual"]:
                    continue
                with st.expander(f"Edit: {opportunity['title']}"):
                    stored_deadline = opportunity["deadline"]
                    picker_value = None
                    if stored_deadline is not None:
                        try:
                            picker_value = date.fromisoformat(stored_deadline)
                        except (TypeError, ValueError):
                            st.warning(
                                f"Stored deadline '{stored_deadline}' "
                                "is not a valid date; saving here replaces it."
                            )

                    with st.form(
                        key=f"edit_opp_{opportunity['id']}",
                        clear_on_submit=False,
                        border=True,
                    ):
                        edit_title = st.text_input(
                            "Title",
                            value=opportunity["title"] or "",
                        )
                        edit_url = st.text_input(
                            "Link (https://...)",
                            value=opportunity["url"] or "",
                        )
                        edit_location = st.text_input(
                            "Location",
                            value=opportunity["location"] or "",
                        )
                        edit_deadline = st.date_input(
                            "Deadline",
                            value=picker_value,
                            format="YYYY-MM-DD",
                            help="Leave empty if unknown.",
                        )
                        clear_deadline = st.checkbox(
                            "Clear the deadline",
                            value=False,
                        )
                        edit_state = st.selectbox(
                            "State",
                            OPPORTUNITY_STATES,
                            index=OPPORTUNITY_STATES.index(
                                opportunity["state"]
                            ),
                        )
                        edit_notes = st.text_area(
                            "Notes",
                            value=opportunity["description_text"] or "",
                        )
                        edit_submitted = st.form_submit_button(
                            "Save opportunity"
                        )

                    if edit_submitted:
                        try:
                            update_manual_opportunity(
                                connection,
                                opportunity["id"],
                                title=edit_title,
                                url=edit_url,
                                location=edit_location,
                                deadline=(
                                    None if clear_deadline else edit_deadline
                                ),
                                notes=edit_notes,
                                state=edit_state,
                            )
                        except (ValueError, LookupError, sqlite3.Error) as error:
                            st.error(str(error))
                        else:
                            st.rerun()

                    with st.form(
                        key=f"del_opp_{opportunity['id']}",
                        clear_on_submit=False,
                        border=True,
                    ):
                        confirm_delete = st.checkbox(
                            "Yes, delete this opportunity",
                            value=False,
                        )
                        delete_submitted = st.form_submit_button(
                            "Delete opportunity"
                        )

                    if delete_submitted:
                        if not confirm_delete:
                            st.error("Tick the box to confirm.")
                        else:
                            try:
                                delete_manual_opportunity(
                                    connection,
                                    opportunity["id"],
                                )
                            except (ValueError, LookupError, sqlite3.Error) as error:
                                st.error(str(error))
                            else:
                                st.rerun()
    except sqlite3.Error as error:
        st.error(f"Database error: {error}")
    finally:
        if connection is not None:
            connection.close()
