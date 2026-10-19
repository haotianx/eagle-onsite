-- Chat schema (.ingest/chat.sqlite). JSON columns hold canonical JSON; timestamps are UTC ISO-8601
-- strings. Ids are random hex strings.

CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    title TEXT,                   -- NULL until generated after the first answer
    scope TEXT,                   -- doc id, or NULL for All documents
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chats_updated ON chats(updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,           -- the question, or the answer's visible text
    content TEXT NOT NULL,        -- JSON list of API messages ({role, content}) this message adds to the history
    state TEXT NOT NULL CHECK (state IN ('generating', 'complete', 'stopped', 'error')),
    error TEXT,                   -- error kind: connection, rate_limit, api, refusal
    scope TEXT,                   -- doc id the question was asked with, or NULL for All documents
    model TEXT,
    usage TEXT,                   -- JSON object: summed token usage incl. cache reads and writes
    ttft_ms REAL,
    total_ms REAL,
    request_ids TEXT NOT NULL DEFAULT '[]',  -- JSON list: Anthropic request id per API call
    created_at TEXT NOT NULL,
    UNIQUE (chat_id, seq)
);

CREATE TABLE IF NOT EXISTS search_steps (
    id INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    round INTEGER NOT NULL,       -- 1-based tool round within the answer
    tool TEXT NOT NULL,           -- search_documents, read_document, list_documents
    query TEXT,                   -- search query, or the list filters as text; NULL for read_document
    scope TEXT,                   -- doc id, or NULL for All documents
    passages TEXT NOT NULL,       -- JSON list of library.Passage / DocumentInfo dicts returned to the model
    error TEXT                    -- error text returned to the model (e.g. tool outside its scope)
);
CREATE INDEX IF NOT EXISTS search_steps_message ON search_steps(message_id, id);

CREATE TABLE IF NOT EXISTS citations (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    n INTEGER NOT NULL,           -- 1-based, in order of first appearance in the answer
    source TEXT NOT NULL,         -- the cited search_result source: chunk id, or doc id from list_documents
    doc_id TEXT NOT NULL,
    chunk_id TEXT,                -- NULL for a document cited from a list
    filename TEXT,
    pages TEXT NOT NULL,          -- JSON list of 1-based page numbers
    regions TEXT NOT NULL,        -- JSON list of {page, x0, y0, x1, y1} (PDF points, top-left origin)
    cited_text TEXT NOT NULL,
    PRIMARY KEY (message_id, n)
);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    filename TEXT NOT NULL,       -- the name shown on the card
    path TEXT,                    -- stored file under the uploads folder; NULL when the doc was already in the library
    pages INTEGER,
    status TEXT NOT NULL CHECK (status IN ('processing', 'ready', 'failed')),
    stage TEXT,                   -- while processing: queued, reading, indexing
    error TEXT,                   -- failure reason shown on the card
    run_id INTEGER,               -- single-doc run in the ingestion catalog
    removable INTEGER NOT NULL,   -- 1 for uploaded docs, 0 for docs from the proposals folder
    removed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS uploads_doc ON uploads(doc_id);

CREATE TABLE IF NOT EXISTS attachments (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    upload_id TEXT NOT NULL REFERENCES uploads(id),
    position INTEGER NOT NULL,
    PRIMARY KEY (message_id, upload_id)
);
