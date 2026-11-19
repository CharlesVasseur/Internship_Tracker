CREATE TABLE IF NOT EXISTS firms (
    id INTEGER PRIMARY KEY,
    profile TEXT NOT NULL,
    name TEXT NOT NULL,
    employer_key TEXT,
    country TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '',
    category TEXT,
    actuarial_depth TEXT,
    role_types TEXT,
    priority TEXT CHECK (priority IN ('P1', 'P2', 'P3', 'P4')),
    careers_url TEXT,
    source_type TEXT,
    source_config TEXT,
    monitoring TEXT NOT NULL DEFAULT 'manual'
        CHECK (monitoring IN ('automated', 'manual')),
    eligibility TEXT,
    internship_type TEXT,
    language_fit TEXT,
    opening_window TEXT,
    work_authorisation TEXT,
    notes TEXT,
    last_verified TEXT,
    last_successful_check TEXT,
    last_check_status TEXT,
    UNIQUE (profile, name, country, city)
);

CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY,
    firm_id INTEGER NOT NULL,
    source_job_id TEXT,
    title TEXT,
    location TEXT,
    url TEXT,
    description_text TEXT,
    language TEXT,
    first_seen TEXT,
    last_seen TEXT,
    missing_count INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'Needs review'
        CHECK (state IN ('Open', 'Upcoming', 'Closed', 'No match', 'Needs review')),
    is_internship INTEGER,
    role_family TEXT,
    season TEXT,
    duration TEXT,
    eligibility_flags TEXT,
    fingerprint TEXT,
    evidence TEXT,
    FOREIGN KEY (firm_id) REFERENCES firms(id)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    firm_id INTEGER NOT NULL,
    opportunity_id INTEGER,
    stage TEXT NOT NULL DEFAULT 'To research' CHECK (stage IN (
        'To research',
        'Contacted',
        'Applied',
        'Online assessment',
        'Online interview',
        'In-person interview',
        'Offer',
        'Rejected'
    )),
    submitted_date TEXT,
    next_action TEXT,
    next_action_date TEXT,
    priority_override TEXT CHECK (priority_override IN ('P1', 'P2', 'P3', 'P4')),
    links TEXT,
    notes TEXT,
    FOREIGN KEY (firm_id) REFERENCES firms(id),
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL,
    type TEXT,
    date TEXT,
    notes TEXT,
    outcome TEXT,
    FOREIGN KEY (application_id) REFERENCES applications(id)
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    firm_id INTEGER NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    result TEXT NOT NULL CHECK (result IN ('ok', 'empty', 'failed', 'blocked')),
    http_status INTEGER,
    n_results INTEGER,
    error TEXT,
    evidence_snippet TEXT,
    FOREIGN KEY (firm_id) REFERENCES firms(id)
);

CREATE TABLE IF NOT EXISTS point_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    application_id INTEGER,
    firm_id INTEGER,
    idempotency_key TEXT NOT NULL UNIQUE,
    points INTEGER NOT NULL,
    created_at TEXT,
    FOREIGN KEY (application_id) REFERENCES applications(id),
    FOREIGN KEY (firm_id) REFERENCES firms(id)
);

CREATE INDEX IF NOT EXISTS idx_firms_profile_employer_key
    ON firms(profile, employer_key);
