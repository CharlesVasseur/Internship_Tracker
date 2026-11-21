# Data model

## firms

Stores one firm row for a profile and country office.

| Column | Type | Meaning | Allowed values |
|---|---|---|---|
| id | INTEGER | Stable row identifier. | |
| profile | TEXT | Profile or owner that contains the firm row. | NOT NULL |
| name | TEXT | Firm name. | NOT NULL |
| employer_key | TEXT | Normalized firm name used to group country offices. | |
| country | TEXT | Country where this office row belongs. | NOT NULL |
| city | TEXT | City of the office. | NOT NULL; defaults to `''` |
| category | TEXT | User-assigned firm category. | |
| actuarial_depth | TEXT | Depth of actuarial work shown by the firm. | |
| role_types | TEXT | Role types listed for the office or firm. | |
| priority | TEXT | Review priority. | P1, P2, P3, P4 |
| careers_url | TEXT | Careers page or direct application link. | |
| source_type | TEXT | Source text classification. | `search_link`, `direct_posting`, `careers_page`, or NULL |
| source_config | TEXT | JSON text with future source configuration. | |
| monitoring | TEXT | Whether monitoring is automated or manual. | NOT NULL; default `manual`; `automated` or `manual` |
| eligibility | TEXT | Eligibility text. | |
| internship_type | TEXT | Internship type text. | |
| language_fit | TEXT | Language fit text. | |
| opening_window | TEXT | Time window for openings. | |
| work_authorisation | TEXT | Work-authorisation requirements. | |
| notes | TEXT | Free-form notes. | |
| last_verified | TEXT | Last date on which the firm or source was checked. | |
| last_successful_check | TEXT | Date of the last successful source check. | |
| last_check_status | TEXT | Status recorded for the latest check. | |

The unique key is `(profile, name, country, city)`. The `profile, employer_key` pair is indexed for grouping and lookup.

`source_type` describes how the saved link was obtained or classified:

- `search_link`: an imported or user-saved search page rather than a direct listing.
- `direct_posting`: a link to a specific job or internship posting.
- `careers_page`: a link to an employer careers or jobs page.
- `NULL`: no source classification is stored yet.

An existing database keeps the profile value it was imported with.

## opportunities

Stores a job opening associated with a firm.

| Column | Type | Meaning | Allowed values |
|---|---|---|---|
| id | INTEGER | Stable row identifier. | |
| firm_id | INTEGER | Firm that owns the opportunity. | NOT NULL; foreign key to `firms.id` |
| source_job_id | TEXT | Identifier assigned by the source. | |
| title | TEXT | Opportunity title. | |
| location | TEXT | Opportunity location. | |
| url | TEXT | Opportunity URL. | |
| description_text | TEXT | Opportunity description. | |
| language | TEXT | Opportunity language. | |
| first_seen | TEXT | Date or timestamp first observed. | |
| last_seen | TEXT | Date or timestamp last observed. | |
| missing_count | INTEGER | Consecutive count of missing observations. | NOT NULL; default 0 |
| state | TEXT | Current opportunity state. | NOT NULL; default `Needs review`; `Open`, `Upcoming`, `Closed`, `No match`, or `Needs review` |
| is_internship | INTEGER | Whether the role is an internship. | |
| role_family | TEXT | Broad role family. | |
| season | TEXT | Internship season. | |
| duration | TEXT | Internship duration. | |
| eligibility_flags | TEXT | Eligibility information flags. | |
| fingerprint | TEXT | Stable identity or deduplicated fingerprint. | |
| evidence | TEXT | Evidence supporting the row or state. | |

## applications

Stores the application process for a firm and optionally a specific opportunity.

| Column | Type | Meaning | Allowed values |
|---|---|---|---|
| id | INTEGER | Stable row identifier. | |
| firm_id | INTEGER | Firm being pursued. | NOT NULL; foreign key to `firms.id` |
| opportunity_id | INTEGER | Specific opportunity, when known. | Nullable; foreign key to `opportunities.id` |
| stage | TEXT | Current application stage. | NOT NULL; default `To research`; `To research`, `Contacted`, `Applied`, `Online assessment`, `Online interview`, `In-person interview`, `Offer`, or `Rejected` |
| submitted_date | TEXT | Date submitted. | |
| next_action | TEXT | Next action to take. | |
| next_action_date | TEXT | Date of the next action. | |
| priority_override | TEXT | Application-specific priority. | P1, P2, P3, P4 |
| links | TEXT | Related application links. | |
| notes | TEXT | Free text from earlier versions. No longer edited in the UI; shown read-only. New notes are written as activity log entries. | |

## activities

Stores dated events belonging to an application.

| Column | Type | Meaning | Allowed values |
|---|---|---|---|
| id | INTEGER | Stable row identifier. | |
| application_id | INTEGER | Application receiving the activity. | NOT NULL; foreign key to `applications.id` |
| type | TEXT | Activity type. | |
| date | TEXT | Date or timestamp of the activity. | |
| notes | TEXT | Activity details. | |
| outcome | TEXT | Outcome or result of the activity. | |

### Activity types

- `application_created`: a new application was created.
- `stage_change`: an application moved from one stage to another.
- `note`: a free-form note was added to the application log.

## checks

Stores the result of one source check for a firm.

| Column | Type | Meaning | Allowed values |
|---|---|---|---|
| id | INTEGER | Stable row identifier. | |
| firm_id | INTEGER | Firm that was checked. | NOT NULL; foreign key to `firms.id` |
| started_at | TEXT | Check start time. | |
| finished_at | TEXT | Check finish time. | |
| result | TEXT | Typed result of the check. | NOT NULL; `ok`, `empty`, `failed`, or `blocked` |
| http_status | INTEGER | HTTP status returned, if any. | |
| n_results | INTEGER | Number of results observed. | |
| error | TEXT | Error description, if the check failed or was blocked. | |
| evidence_snippet | TEXT | Short evidence recorded for the result. | |

## point_events

Stores scoring events with an application or firm attachment.

| Column | Type | Meaning | Allowed values |
|---|---|---|---|
| id | INTEGER | Stable row identifier. | |
| event_type | TEXT | Type of scoring event. | NOT NULL |
| application_id | INTEGER | Application associated with the event. | Nullable; foreign key to `applications.id` |
| firm_id | INTEGER | Firm associated with the event. | Nullable; foreign key to `firms.id` |
| idempotency_key | TEXT | Key preventing the same event from being scored twice. | NOT NULL; UNIQUE |
| points | INTEGER | Number of points awarded. | NOT NULL |
| created_at | TEXT | Date or timestamp the event was recorded. | |

## Relations

- Firms have 1..n applications.
- Firms have 1..n opportunities.
- Applications have 1..n activities.
- A point event is optionally associated with an application or a firm; `idempotency_key` is UNIQUE.
- An application can optionally refer to one opportunity.

## Why one firms row per country office

A firm that has offices in several countries has one row per country. `employer_key` groups those office rows into the same employer. The unique key is `(profile, name, country, city)`.

A separate sources table is not built yet.

## Not built yet

The `opportunities`, `checks`, and `point_events` tables are created, but nothing writes to them yet. The Excel importer writes firms and applications only. Opportunities, checks, and point_events are still unused.
