CREATE TABLE IF NOT EXISTS processed_sources (
    url_hash TEXT PRIMARY KEY,
    url TEXT,
    title TEXT,
    source_type TEXT,
    processed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS risk_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    url_hash TEXT REFERENCES processed_sources(url_hash),
    url TEXT,
    company_name TEXT,
    ticker TEXT,
    is_negative_event BOOLEAN,
    severity_score INT,
    category TEXT,
    key_impact TEXT,
    raw_snippet TEXT,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS watched_companies (
    ticker TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_events_company_severity
    ON risk_events(company_name, severity_score);

CREATE INDEX IF NOT EXISTS idx_risk_events_published_at
    ON risk_events(published_at DESC);

CREATE INDEX IF NOT EXISTS idx_watched_companies_active
    ON watched_companies(is_active);

-- Run once on existing projects that already created the tables:
-- ALTER TABLE risk_events ADD COLUMN IF NOT EXISTS url TEXT;
-- ALTER TABLE risk_events ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
-- CREATE INDEX IF NOT EXISTS idx_risk_events_published_at ON risk_events(published_at DESC);
-- CREATE TABLE IF NOT EXISTS watched_companies (
--     ticker TEXT PRIMARY KEY,
--     company_name TEXT NOT NULL,
--     is_active BOOLEAN NOT NULL DEFAULT TRUE,
--     created_at TIMESTAMPTZ DEFAULT NOW()
-- );
