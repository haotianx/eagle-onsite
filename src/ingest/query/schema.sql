-- Chat store (Query-Design §14). JSON columns hold JSON text; timestamps are UTC ISO-8601 strings.

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY,
    title TEXT,
    scope TEXT,                   -- doc id; NULL = All documents
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    chat INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,        -- JSON list: the API messages ({role, content}) this message adds to the history
    state TEXT NOT NULL CHECK (state IN ('generating', 'complete', 'stopped', 'error')),
    error TEXT,                   -- error kind: connection, rate_limit, api_error, refusal
    scope TEXT,
    model TEXT,
    usage TEXT,                   -- JSON object
    latency TEXT,                 -- JSON object
    request_id TEXT,
    created TEXT NOT NULL,
    UNIQUE (chat, seq)
);

CREATE TABLE IF NOT EXISTS search_steps (
    id INTEGER PRIMARY KEY,
    message INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    round INTEGER NOT NULL,
    tool TEXT NOT NULL,
    query TEXT,
    scope TEXT,
    passages TEXT NOT NULL        -- JSON list of objects (chunk_id, doc_id, pages, score, …)
);
CREATE INDEX IF NOT EXISTS search_steps_message ON search_steps(message, id);

CREATE TABLE IF NOT EXISTS citations (
    message INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    n INTEGER NOT NULL,
    doc_id TEXT NOT NULL,
    chunk_id TEXT,
    filename TEXT,
    pages TEXT NOT NULL,          -- JSON list
    regions TEXT NOT NULL,        -- JSON list of {page, x0, y0, x1, y1}
    cited_text TEXT,
    PRIMARY KEY (message, n)
);

CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY,
    doc_id TEXT,
    filename TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'ready', 'failed')),
    error TEXT,
    removed INTEGER NOT NULL DEFAULT 0,
    removable INTEGER NOT NULL,   -- 0 for documents that come from the proposals folder
    run_id INTEGER,               -- the single-doc run processing it
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS uploads_doc ON uploads(doc_id);

CREATE TABLE IF NOT EXISTS attachments (
    message INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    upload INTEGER NOT NULL REFERENCES uploads(id),
    PRIMARY KEY (message, upload)
);
