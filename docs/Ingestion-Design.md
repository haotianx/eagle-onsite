# Document ingestion pipeline — design

## 1. Goal

Ingest the firm's proposal PDFs and process them through an engineer-defined DAG (default: parse/OCR → chunk → embed). The end result is a table with one row per chunk — text, provenance, document metadata and a semantic vector column — that a later system can search semantically. Each node is a Python script whose output is an artifact identified by DAG + node. Engineers swap part or all of the DAG, reprocess incrementally, and judge changes in a UI.

**Scope: a local demo.** Source, runs, artifact store and UI all live on one Apple Silicon Mac (M5, 32 GB). Shared servers, multi-user concurrency and access control are out of scope.

## 2. Corpus (`/Users/Shared/proposals`)

- ~1,150 PDFs, 857 MB; layout `<job #> <client/project>/CONTRACT/<file>.pdf`; mostly 1–20 pages (~10K pages).
- ~80% born-digital with a text layer; ~20% image-only copier scans; many signed files are born-digital plus scanned signature pages, so OCR is decided per page.
- Fee schedules in tables; boilerplate terms; 32 byte-identical extra copies; signed/unsigned near-duplicates.

## 3. Concepts

| Term | Meaning |
| --- | --- |
| Source | A named local folder of documents. |
| Snapshot | Immutable manifest of a source at sync time. Doc id = SHA-256 of the file; identical files collapse to one doc listing all paths. |
| Script | A reusable Python file implementing the node contract. |
| Checks file | An optional Python file with script-declared validation for a node. |
| DAG | A named YAML layout wiring scripts into nodes over a source. |
| Node | One step in a DAG: script + params + inputs (+ optional checks). |
| Artifact | A node's output, addressed `<dag>/<node>`: a sequence of immutable versions with records per document. |
| Version | One build of an artifact, labeled `<dag>/<node>@vN`. Candidate until promoted; at most one current version per artifact. |
| Run | One execution of selected nodes of a DAG over a snapshot or a doc subset. |

## 4. Principles

Reproducibility (same code + inputs → same result; lineage on every version) · all-or-nothing versions · no lock-in in the authoring contract or storage format · iterate on real data without disturbing what's current.

## 5. DAG definition

- One YAML file per DAG in git (`dags/<name>.yaml`); scripts in git (`nodes/*.py`, checks in `nodes/checks/*.py`). Authoring is git-only; the UI views and runs DAGs but never edits them.
- Inputs reference node names; the root node reads the DAG's `source`. Nodes may have several inputs; a DAG may have several terminal nodes. No per-doc routing in YAML — do that inside a script.
- No cross-DAG references; DAGs share work through the per-doc cache — an identical node in another DAG reuses the same cached outputs (§7).

```yaml
name: default
source: proposals
nodes:
  ocr:
    script: nodes/docling_parse.py
    params: { ocr_engine: ocrmac }
    timeout: 300            # seconds per doc; not part of the fingerprint
    workers: 4              # parallel worker processes; not part of the fingerprint
  chunk:
    script: nodes/hybrid_chunk.py
    inputs: [ocr]
    params: { max_tokens: 512, tokenizer: Qwen/Qwen3-Embedding-0.6B }
  embed:
    script: nodes/embed.py
    inputs: [chunk]
    params: { model: Qwen/Qwen3-Embedding-0.6B }
    workers: 1              # one process on the GPU
    # checks: nodes/checks/embed.py   (optional)
```

## 6. Node scripts

- `setup(params) -> ctx` — optional; once per worker process (load models).
- `run(doc, inputs, params, ctx) -> records` — per document. `doc` = id, paths, size, file path. The root node's input is the PDF; other nodes receive their input nodes' records for this doc. Return a `dict` (stored as JSON), or `list[dict]` or a `pyarrow.Table` (stored as Parquet).
- `run_corpus(docs, inputs, params, ctx) -> records` — whole corpus; records keyed by doc id plus optional corpus-wide files; cached as a whole.
- A script defines `run` or `run_corpus`; the framework detects which.
- `ARTIFACT_TYPE = "parse" | "chunk" | "embed"` — optional module constant that opts a script into the built-in checks (§10) and Compare metrics for that type.
- `describe(params) -> dict` — optional; version metadata stored with every version (e.g. model id, revision, dimension, metric, query instruction).
- Node YAML `timeout` and `workers` control execution only and are not part of the fingerprint.
- Scripts log with standard Python `logging`; the framework captures it (§11). `ctx.debug(name, data)` saves a debug file (e.g. a rendered page image or intermediate JSON) for the current doc — kept in scratch runs only, a no-op otherwise.
- All scripts share one locked Python environment (uv lockfile in git). Model weights are pinned by revision in params.

## 7. Artifacts, versions, promotion

- A node's fingerprint = hash(script hash, params, environment hash, input nodes' fingerprints). It chains through upstream code and params but not through which docs exist, so adding docs never changes it. Checks are fingerprinted separately (§10).
- Per-doc outputs are cached by (node fingerprint, doc hash). Outputs are stored under their own content hash, and a cache index maps (node fingerprint, doc hash) → output, so a forced recompute never alters what older versions reference.
- A version is a manifest of doc → output for one snapshot, so reused docs cost nothing. Lineage: node fingerprint, input versions, snapshot, git commit, dirty flag, script hash, params, lockfile and model revisions, timing.
- Runs produce **candidate** versions; an engineer **promotes** one to make it current. Promotion is per node and does not promote upstream versions. Rollback = promote an older version.
- Promotable only if built over the full snapshot from committed code. Subset or working-tree runs are **scratch**: inspectable and searchable, never promotable.
- Node states: fresh · stale (code or params changed, or current version built from inputs that aren't current) · candidate available · running · failed · never run.
- Retention: keep everything; versions can be deleted manually.

## 8. Runs and incremental reprocessing

A run is defined by:
- **Snapshot**: latest by default, or a chosen one. Syncing is manual.
- **Nodes**: one node · node + downstream · node + upstream · whole DAG.
- **Input versions**: current by default, or any chosen version. Within a run, downstream nodes read what the run just produced.
- **Recompute**: reuse (skip docs already cached for this fingerprint) or force.
- **Documents**: all, a sample of N, or specific docs (subset → scratch).
- **Code**: committed (promotable) or working tree (scratch).

Examples: swap the chunker and run `chunk` alone over current OCR · run `chunk` + downstream · force the whole DAG · sync new proposals and rerun — per-doc nodes process only the new docs.

One run per `<dag>/<node>` at a time; triggers arriving meanwhile merge into one follow-up run. Runs start manually from the UI or CLI.

## 9. Deduplication

- **Exact duplicate files**: collapse to one doc (content hash) that lists every path.
- **Near-duplicate documents** (signed vs unsigned, re-saved copies): separate docs in v1.
- **Repeated chunks** (boilerplate): each chunk row carries `text_hash` (hash of normalized text); the published table adds `dup_count` (number of docs in the version sharing that hash). Nothing is dropped; later search can collapse or down-weight.

## 10. Validation

- **DAG** (on load and `ingest validate`): cycles, missing scripts or checks files, unknown inputs, each script defines `run` or `run_corpus`, params are plain values.
- **Sync**: corrupt, encrypted or empty PDFs are flagged in the snapshot with a reason and kept, so `ocr` records a clear per-doc error.
- **Hard checks** (built-in, always on): record shape and types for built-in artifacts; vectors have the model's dimension, no NaN, unit norm; one vector per chunk; coverage — every snapshot doc ends processed, failed, or skipped-upstream. A failure makes that doc an error; the version still publishes.
- **Quality checks** (built-in, warnings): pages with no text, chunks over `max_tokens`, docs with zero chunks.
- **Script-declared checks**: a node's `checks:` file defines `validate(record, params) -> issues`, each `{level: error | warning, code, message, page?}`. Errors make the doc an error; warnings show with the built-in ones. The checks file has its own fingerprint, so editing it never makes the node stale. **Revalidate** re-runs checks over an existing version and replaces its issues without changing records or version number. Issues are stored per (version, checks fingerprint).
- Issues and warnings appear in the Version view and the Promote dialog; nothing blocks promotion.

## 11. Logging, debugging and telemetry

**Logging**
- Structured JSON-lines logs on disk; human-readable output in the CLI. Default level INFO, `--log-level` to change.
- Every line carries run, DAG, node, version and doc id, so logs filter to one node or one document.
- Captured: engine events (planned, cached, started, finished, failed, with timings); per-doc output of each `run()` call — Python `logging`, `warnings`, stdout and stderr, including Docling and transformers warnings; API server logs.
- Stored under `.ingest/runs/<run id>/`; deleting a version doesn't delete its run's logs.

**Debugging**
- Per-doc errors record exception type, message, full traceback, script line, input versions, duration and worker process.
- Fault isolation: a per-doc `timeout` (node YAML, default 300 s) catches PDFs that hang a parser; a worker crash (native segfault, Metal out-of-memory) fails only the doc it was processing, and the worker is replaced.
- Crash safety: per-doc outputs are written atomically and a version's manifest only at the end, so a crashed or cancelled run leaves no partial version and a rerun reuses every doc that finished.
- Runs can be cancelled from the UI or CLI.
- To reproduce a failure, rerun that doc as a scratch run (Run launcher → Documents → Pick), with logs, traceback and debug files to inspect.
- `ingest doctor` checks the environment: Python version, Metal availability, ocrmac, downloaded models, source readable, free disk space.

**Telemetry**
- Kept in the catalog and shown in the UI; no OpenTelemetry export in this phase.
- Recorded per run, node and doc: wall time, pages per second, cache hit rate, failure rate, share of OCR'd pages, model load time, peak memory.
- Run detail shows per-node timings and the slowest docs; Version and Document views show per-doc timing.
- Nothing leaves the machine: Hugging Face telemetry disabled; models fetched once with `ingest models pull` at pinned revisions, then runs work offline; no usage analytics in the UI.

## 12. Output for semantic search

- **Vector table**: one row per chunk — `chunk_id` (hash of doc id + text), `doc_id`, `seq`, `text`, `embed_text`, `pages`, `section`, `regions[{page, bbox}]`, `text_hash`, `vector` (float32[1024], L2-normalized) — joined at load time with `dup_count` and document metadata from the version's snapshot: `paths`, and `job_number`, `project`, `subfolder`, `filename` parsed from the primary path (e.g. `2292 SZEKERES/CONTRACT/AEROTECH/…`). Joining at load time keeps cached outputs independent of file locations.
- **Version metadata**: model id and revision, dimension, metric (cosine = dot product on normalized vectors), and the Qwen3 query instruction, so later search embeds queries identically.
- **Access**: `ingest.load_table("default/embed")` (current, or `@vN`) returns an Arrow table; promoting a version writes `.ingest/published/<dag>/<node>.parquet`.
- **Originals** are referenced in place (path + hash), not copied. If a file later changes or disappears, old versions show "original changed or missing" instead of the PDF.
- The text column stays alongside vectors so lexical search can be added later.

## 13. Default DAG (local, Apple Silicon)

- `ocr` — Docling standard pipeline: text layer for born-digital pages; Apple Vision (ocrmac) only for bitmap regions/pages; layout, reading order, TableFormer tables, page + bbox provenance. Output: Docling document JSON.
- `chunk` — Docling HybridChunker: section-structured, ~512 tokens on the Qwen3-Embedding tokenizer, merges small neighbors, repeats table headers. `embed_text` = header (doc title, path, section path) + chunk text.
- `embed` — Qwen3-Embedding-0.6B on Metal (MPS), single process, batched. Output: the vector table rows in §12.
- Swap-in candidates: PaddleOCR-VL-1.6 for scans (renders every page as an image and ignores the text layer, so it fits only behind Docling); Granite-Docling VLM; RapidOCR (portable off macOS); Qwen3-Embedding-4B; Qwen3-VL-Embedding-2B (page-image retrieval); harrier-oss-v1.
- Tradeoff: ocrmac ties scanned-page OCR to macOS — acceptable for this demo; RapidOCR is the portable swap.

## 14. Engineer experience (UI + CLI)

Local web app; CLI with the same capabilities: `ingest sync | validate | run | cancel | status | show | diff | promote | revalidate | logs | doctor | models pull | ui`.

Navigation: DAGs · Runs · Search · Sources.

1. **DAGs** — DAGs from git: source, last run, node-state summary, uncommitted-changes badge.
2. **DAG detail** — read-only auto-laid-out graph with state badges. Node side panel: script, params, checks, current / candidate / scratch versions, doc and failure counts; Run…, Promote, Open artifact.
3. **Run launcher** (drawer, preselected from where it's opened) — nodes, snapshot, per-input versions, reuse / force, all / sample N / pick, committed / working tree (preselected to working tree when the selected nodes have uncommitted changes). Shows per node "to process · cached" and a Scratch or Promotable badge.
4. **Runs** — status, duration, processed / cached / failed; Cancel. Run detail: live per-node progress, logs filterable by node, doc and level, per-doc errors with tracebacks, per-node timings and slowest docs, versions produced, lineage.
5. **Version** — lineage, counts, issues (errors and warnings, filterable), documents (filter failed, find by filename or job #); Promote; Compare with…; Revalidate (flagged when the checks file changed).
6. **Document** — PDF left; tab per node with a version picker right; hovering a parsed element or chunk highlights its region on the PDF. Each tab also shows that doc's error and traceback, issues, logs, timing and debug files (scratch versions).
7. **Compare** — two versions: summary metrics (generic: docs, records, failures, size; parse: pages, OCR'd pages, tables; chunk: chunks, tokens per chunk; embed: vectors, model), changed docs sorted by size of change, per-doc side-by-side with changed boundaries marked.
8. **Search** — one query over two embed versions (any, incl. candidates and scratch; default current vs newest candidate); top 10 per side, adjustable; rank and rank changes ("new", "was N") lead, raw scores small and secondary since scores aren't comparable across versions; results link to the PDF page.
9. **Sources** — snapshots, Sync, snapshot diff (added / removed / changed).

Promote confirmation names the version being replaced, lists issues, and warns when the version was built from inputs that aren't current. The UI watches the repo (stale and uncommitted badges update live); the source changes only on Sync. UI kit: Tailwind + shadcn/ui; clean, no helptext.

## 15. Implementation

- **Stack**: Python 3.13 (uv-managed; system Python 3.15 is too new for torch/Docling wheels); FastAPI + SSE; React + TypeScript (Vite), PDF.js, a graph library; typed client generated from OpenAPI.
- **Engine**: custom and small — plans node order, resolves input versions, fingerprints, skips cached docs, runs per-doc nodes on a process pool and GPU nodes in one batched process, runs validation, writes versions. Runs execute in a worker subprocess so the API stays responsive.
- **Storage**: SQLite catalog (single writer: the run's parent process) + content-addressed objects `objects/<sha[:2]>/<sha>.{json,parquet}` and a cache index (node fingerprint, doc hash) → object, under `.ingest/` at the repo root (gitignored; overridable via `ingest.toml` or `INGEST_DATA_DIR`).
- **Catalog tables**: sources, snapshots, snapshot_docs (incl. path metadata and sync flags), versions, version_docs (incl. error, traceback, timing, peak memory), version_issues, current, runs, run_events, run_metrics. Logs and debug files live on disk under `.ingest/runs/<run id>/`, indexed by node and doc.
- **Search (UI)**: exact cosine over a version's vectors in memory (~41K × 1024); queries embedded with each side's recorded model.

## 16. Repo layout

```
pyproject.toml  uv.lock  ingest.toml  .gitignore (.ingest/, web/node_modules/, web/dist/)
dags/default.yaml
nodes/docling_parse.py  nodes/hybrid_chunk.py  nodes/embed.py  nodes/checks/
src/ingest/
  contract.py      shared types: Doc, NodeContext, Issue, record helpers
  scripts.py       script and checks loading (setup / run / run_corpus / describe detection)
  spec.py          DAG load + validation
  fingerprint.py   script, params, env, checks, node/version fingerprints
  gitinfo.py       HEAD, dirty paths per node, repo watcher
  config.py        ingest.toml + env
  store/catalog.py store/objects.py store/schema.sql
  sources.py       sync → snapshot, path metadata, diff
  validation.py    hard, quality and script-declared checks; revalidate
  obs.py           logging setup, per-doc capture, metrics, debug files
  engine/planner.py engine/executor.py engine/worker.py
  state.py         node states, promotion rules
  publish.py       load_table, dup_count, published Parquet
  metrics.py       Compare metrics
  search.py        in-memory search
  api/             FastAPI routes + SSE
  cli.py
tests/fixtures/proposals/   real PDFs, original folder structure
tests/
web/
```

**API**: `GET /dags`, `GET /dags/{dag}`, `GET /dags/{dag}/nodes/{node}/versions`, `POST /runs/plan`, `POST /runs`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events`, `GET /versions/{id}`, `GET /versions/{id}/docs`, `GET /versions/{id}/docs/{doc}`, `GET /versions/{id}/issues`, `POST /versions/{id}/revalidate`, `POST /versions/{id}/promote`, `POST /runs/{id}/cancel`, `GET /runs/{id}/logs?node=&doc=&level=`, `GET /runs/{id}/metrics`, `GET /versions/{id}/docs/{doc}/debug`, `GET /compare?a=&b=`, `POST /search`, `GET /docs/{doc}/pdf`, `GET /sources`, `POST /sources/{name}/sync`, `GET /snapshots/{id}/diff`.

## 17. Build plan

Implemented with the repo's `parallel-implement` skill: scaffolding and the shared interfaces (§6, §10, §11, §12, §15, API) first, then one subagent per component on non-overlapping files; each tests and commits locally (no push), on the current branch.

- **Phase 1 — engine slice**, in dependency waves so each component tests against real code:
  - Wave 1: (A) config, spec, scripts, fingerprint, gitinfo · (B) catalog, objects · (D) default nodes · (F) logging, run metrics, debug files
  - Wave 2: (C) sources and path metadata · (E) validation · (H) publish and Compare metrics
  - Wave 3: (G) planner, executor and worker (timeouts, crash isolation, cancel), state
  - Wave 4: (I) CLI incl. doctor and models pull
  - Gate: CLI end to end on fixtures, then 25 real proposals.
- **Phase 2 — API and UI**: (J) API, search · (K) web scaffold and shared components · then (L) DAGs, DAG detail, Run launcher · (M) Runs (logs, timings, cancel) · (N) Version, Compare · (O) Document · (P) Search · (Q) Sources.

**Fixtures** (~1.3 MB, real proposals including client names and fees, folder structure kept):
- `2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf` — born-digital, fees, 2 pages
- `3002 ELLIOT/CONTRACT/Mike Proposal_Elliot Pickle Ball.pdf` — born-digital, multi-section, 9 pages
- `3426 AGUILAR COLINA FUERTE/CONTRACT/PROPOSAL ADDENDUM.pdf` — image-only scan
- `3649 Murphy Bella Vista/CONTRACT/Murphy - New Job Form & Signed Proposal.pdf` — mixed
- `3748 GROVE PLAZA/CONTRACT/Signed Proposal - Grove Plaza Civil.pdf` — mixed, 13 pages
- `3257 YARDLEY- ARCHIVE FOLDER/CONTRACT/STAKING PROPOSAL.pdf` + `3257 YARDLEY/REFERENCE/3257 Yardley - ARCHIVE FOLDER/CONTRACT/STAKING PROPOSAL.pdf` — byte-identical pair

## 18. Licenses

| Component | License | Unrestricted commercial use |
| --- | --- | --- |
| Docling, HybridChunker | MIT | Yes |
| Docling Heron layout model | Apache 2.0 | Yes |
| Docling TableFormer weights | CDLA-Permissive-2.0 | Yes |
| Apple Vision via ocrmac | macOS framework; ocrmac wrapper MIT | Yes on Apple hardware |
| Qwen3-Embedding-0.6B | Apache 2.0 | Yes |
| Swap-ins: PaddleOCR-VL-1.6, Qwen3-VL-Embedding (Apache 2.0), harrier-oss-v1 (MIT) | as listed | Yes — confirm on official model pages |
| Excluded: Marker/Surya, Chandra 2 (modified OpenRAIL-M), jina-v5 (CC BY-NC), MinerU (unverified) | — | No / unknown |

## 19. Smaller defaults

Every PDF under the source root is included (optional glob filter); non-PDFs skipped · a changed file is a new doc; a deleted file drops out of new versions · signed and unsigned versions stay separate.

## 20. Deferred

Schedules and watching the share · `smb://` and non-PDF sources · shared server and multi-user · production search serving and a lexical index · near-duplicate documents · golden-query metrics · version-level custom checks · OpenTelemetry export and alerting · a dedicated single-doc debug command.

## 21. Verification

- Component tests (`uv run pytest`; Vitest for web).
- End to end on fixtures via CLI:
  - `ingest sync`: 6 docs from 7 files; a truncated PDF generated at test time is flagged; resync without changes shows no diff.
  - `ingest run default`: the scanned addendum gets OCR text; Szekeres fees ($1,750.00, $1,835.00) appear verbatim.
  - `load_table("default/embed")`: every row has a unit-norm 1024-dim vector, metadata and `text_hash`; querying "aerial mapping fee" returns the Szekeres fee chunk first.
  - Change `max_tokens` → chunk and embed stale; `--node chunk` processes 0 OCR docs; `--from chunk` rebuilds embed; `--force` recomputes all.
  - A checks file flags chunks without a section: warnings appear; editing it leaves the node fresh; Revalidate updates issues.
  - Working-tree runs are scratch and can't be promoted; promoting chunk alone leaves embed stale; promoting embed writes the published Parquet.
  - Test nodes that sleep past `timeout`, crash the worker (`os._exit`) and raise: each fails only its doc with the right error; other docs complete. Cancelling mid-run leaves no partial version; rerunning reuses finished docs.
  - `ingest logs <run> --doc <id>` returns only that doc's lines, including captured script warnings; a scratch run's `ctx.debug` files appear for the doc and are absent from promotable runs.
  - With `HF_HUB_OFFLINE=1` after `ingest models pull`, a full run succeeds; `ingest doctor` reports all checks passing.
- Opt-in: `INGEST_REAL_CORPUS=1 uv run pytest -m corpus` over 25 sampled real proposals.
- UI walk-through in the browser pane: swap chunker → scratch run on 25 docs → Document highlights → Compare → Search → commit → full run → promote; Sync with an added PDF → incremental run.
