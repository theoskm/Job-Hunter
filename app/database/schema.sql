-- One canonical row per real-world vacancy.
-- The same job appearing on Reed and Adzuna produces ONE row here
-- and TWO rows in job_sources.

CREATE TABLE IF NOT EXISTS jobs (
    job_id                TEXT PRIMARY KEY,
    canonical_key         TEXT NOT NULL UNIQUE,

    -- Core details
    title                 TEXT NOT NULL,
    company               TEXT NOT NULL,
    location              TEXT,
    salary_min            REAL,
    salary_max            REAL,
    salary_currency       TEXT DEFAULT 'GBP',
    salary_raw            TEXT,
    employment_type       TEXT,
    working_arrangement   TEXT,          -- office / hybrid / remote / unknown
    date_posted           TEXT,          -- ISO-8601 date
    closing_date          TEXT,
    description           TEXT,
    requirements          TEXT,
    company_website       TEXT,
    recruiter             TEXT,

    -- Preferred application route
    direct_application_url TEXT,

    -- Scoring (populated in Phase 6)
    score                 INTEGER,
    priority              TEXT,
    career_fit            INTEGER,
    transferable_skills   INTEGER,
    accessibility         INTEGER,
    skills_match          INTEGER,
    salary_score          INTEGER,
    location_score        INTEGER,
    career_development    INTEGER,
    matching_skills       TEXT,          -- JSON array
    skill_gaps            TEXT,          -- JSON array
    reasoning             TEXT,

    -- CRM state. status_source guards manual edits: the automation
    -- must never overwrite a status the user set themselves.
    status                TEXT NOT NULL DEFAULT 'NEW',
    status_source         TEXT NOT NULL DEFAULT 'system',   -- 'system' | 'user'
    cv_version            TEXT,
    cover_letter_required INTEGER DEFAULT 0,
    application_date      TEXT,
    follow_up_date        TEXT,
    interview_date        TEXT,
    rejection_reason      TEXT,
    notes                 TEXT,

    -- Cache control: avoids re-paying for AI analysis of unchanged jobs
    description_hash      TEXT,
    analysis_hash         TEXT,

    -- Lifecycle
    first_seen            TEXT NOT NULL,
    last_updated          TEXT NOT NULL,
    last_checked          TEXT,
    is_expired            INTEGER NOT NULL DEFAULT 0,
    alert_sent_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_score      ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_posted     ON jobs(date_posted DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_canonical  ON jobs(canonical_key);

-- Every place a given job was found.
CREATE TABLE IF NOT EXISTS job_sources (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT NOT NULL,
    source           TEXT NOT NULL,      -- 'reed' | 'adzuna' | 'indeed' | ...
    source_job_id    TEXT,
    source_url       TEXT NOT NULL,
    first_seen       TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE,
    UNIQUE (job_id, source, source_url)
);

CREATE INDEX IF NOT EXISTS idx_sources_job ON job_sources(job_id);

-- Audit trail so the learning loop (Phase 10) can measure outcomes.
CREATE TABLE IF NOT EXISTS status_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    old_status   TEXT,
    new_status   TEXT NOT NULL,
    changed_by   TEXT NOT NULL,          -- 'system' | 'user'
    changed_at   TEXT NOT NULL,
    note         TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);

-- Per-run logging so a failing source never silently disappears.
CREATE TABLE IF NOT EXISTS run_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_started       TEXT NOT NULL,
    run_finished      TEXT,
    source            TEXT,
    jobs_found        INTEGER DEFAULT 0,
    jobs_new          INTEGER DEFAULT 0,
    jobs_duplicate    INTEGER DEFAULT 0,
    errors            TEXT,
    status            TEXT                -- 'success' | 'partial' | 'failed'
);
