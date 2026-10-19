-- Catalog schema. JSON columns hold canonical JSON; timestamps are UTC ISO-8601 strings.

CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL REFERENCES sources(name),
    created_at TEXT NOT NULL,
    doc_count INTEGER NOT NULL,
    file_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_source ON snapshots(source, id);

CREATE TABLE IF NOT EXISTS snapshot_docs (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    primary_path TEXT NOT NULL,
    paths TEXT NOT NULL,          -- JSON list
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    meta TEXT NOT NULL,           -- JSON object
    flags TEXT NOT NULL,          -- JSON list
    flag_reason TEXT,
    PRIMARY KEY (snapshot_id, doc_id)
);
CREATE INDEX IF NOT EXISTS snapshot_docs_path ON snapshot_docs(snapshot_id, primary_path);

CREATE TABLE IF NOT EXISTS file_index (
    source TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha TEXT NOT NULL,
    PRIMARY KEY (source, rel_path)
);

CREATE TABLE IF NOT EXISTS cache (
    node_fp TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    object_sha TEXT NOT NULL,
    kind TEXT NOT NULL,
    duration_s REAL,
    metrics TEXT NOT NULL DEFAULT '{}',   -- JSON object: metrics.doc_metrics of the output
    created_at TEXT NOT NULL,
    PRIMARY KEY (node_fp, doc_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    dag TEXT NOT NULL,
    request TEXT NOT NULL,        -- JSON object
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    scratch INTEGER NOT NULL,
    git_commit TEXT,
    dirty INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'dag' CHECK (kind IN ('dag', 'single_doc'))
);
CREATE INDEX IF NOT EXISTS runs_dag ON runs(dag, id);
CREATE INDEX IF NOT EXISTS runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL         -- JSON object
);
CREATE INDEX IF NOT EXISTS run_events_run ON run_events(run_id, id);

CREATE TABLE IF NOT EXISTS run_metrics (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node TEXT,
    doc_id TEXT,
    name TEXT NOT NULL,
    value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS run_metrics_run ON run_metrics(run_id, id);

CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY,
    dag TEXT NOT NULL,
    node TEXT NOT NULL,
    seq INTEGER,
    node_fp TEXT NOT NULL,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    input_versions TEXT NOT NULL, -- JSON object: input node -> version id
    run_id INTEGER REFERENCES runs(id),
    git_commit TEXT,
    dirty INTEGER NOT NULL,
    scratch INTEGER NOT NULL,
    artifact_type TEXT,
    meta TEXT NOT NULL,           -- JSON object
    stats TEXT NOT NULL,          -- JSON object
    status TEXT NOT NULL CHECK (status IN ('building', 'complete', 'abandoned')),
    created_at TEXT NOT NULL,
    finalized_at TEXT,
    UNIQUE (dag, node, seq)
);
CREATE INDEX IF NOT EXISTS versions_run ON versions(run_id);

CREATE TABLE IF NOT EXISTS version_docs (
    version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'skipped')),
    object_sha TEXT,
    kind TEXT,
    cached INTEGER NOT NULL,
    error_type TEXT,
    error_message TEXT,
    traceback TEXT,
    duration_s REAL,
    peak_rss_mb REAL,
    worker_pid INTEGER,
    metrics TEXT NOT NULL DEFAULT '{}',   -- JSON object: metrics.doc_metrics of the output
    PRIMARY KEY (version_id, doc_id)
);

CREATE TABLE IF NOT EXISTS version_issues (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    doc_id TEXT,
    source TEXT NOT NULL CHECK (source IN ('hard', 'quality', 'script')),
    checks_fp TEXT,
    level TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    page INTEGER
);
CREATE INDEX IF NOT EXISTS version_issues_version ON version_issues(version_id, source);

CREATE TABLE IF NOT EXISTS current (
    dag TEXT NOT NULL,
    node TEXT NOT NULL,
    version_id INTEGER NOT NULL REFERENCES versions(id),
    promoted_at TEXT NOT NULL,
    PRIMARY KEY (dag, node)
);

CREATE TABLE IF NOT EXISTS pending_docs (
    doc_id TEXT NOT NULL,
    version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    paths TEXT NOT NULL,          -- JSON list
    meta TEXT NOT NULL,           -- JSON object
    object_sha TEXT NOT NULL,
    pages INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, version_id)
);
CREATE INDEX IF NOT EXISTS pending_docs_version ON pending_docs(version_id, created_at);

CREATE TABLE IF NOT EXISTS removed_docs (
    doc_id TEXT PRIMARY KEY,
    removed_at TEXT NOT NULL
);
