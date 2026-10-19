# Debrief — 2026-10-19

## Outcome

- Designed and built a local document ingestion pipeline for the ~1,150 proposal PDFs: engineer-defined DAGs of Python nodes (default: parse/OCR → chunk → embed) producing a semantic vector table for later search. Designs: [Ingestion-Design.md](Ingestion-Design.md), [Query-Design.md](Query-Design.md).
- **Phase 1** (engine, CLI, single-doc runs, library view) and **Phase 2** (engineer UI + API) are on `main` and pushed. **Phase 3** (query app) is complete on `claude/phase-3-query-app-b67451`, not merged.
- The first full-corpus run (1,117 docs) is in progress; its versions will be promoted when it finishes so the chat library goes live.

## Key product decisions

| Topic | Decision |
| --- | --- |
| Scope | Local demo on one Apple Silicon Mac (M5, 32 GB); no shared server or multi-user |
| DAG format | YAML layout per DAG (`dags/*.yaml`), separate from scripts (`nodes/*.py`); git is the only authoring path |
| Script contract | `run(doc, inputs, params, ctx)` per document or `run_corpus(...)`; optional `setup`, `describe`, `ARTIFACT_TYPE`; YAML `timeout`/`workers` |
| Artifacts | `<dag>/<node>` holds immutable versions (`@vN`); content-addressed per-doc cache keyed by (node fingerprint, doc hash) |
| Promotion | Explicit and per node; scratch versions (doc subsets, uncommitted code) are never promotable |
| Reprocessing | Choose nodes (one / + downstream / + upstream / all), reuse or force, all or a sample of docs, committed or working-tree code |
| Deduplication | Identical files collapse by content hash; near-duplicate docs stay separate; boilerplate chunks carry `text_hash` + `dup_count` |
| Validation | DAG, sync and built-in hard checks (fail the doc), quality checks (warnings), script-declared checks in a separate file with Revalidate |
| Observability | Per-doc tagged JSON logs, timeouts, crash isolation, catalog metrics; no OpenTelemetry; nothing leaves the machine (models pulled once, offline runs) |
| Output | One self-contained Parquet row per chunk with a normalized 1024-dim vector, path metadata and version metadata (model, revision, query instruction) |
| Sources | A source is a set of folders: `/Users/Shared/proposals` and `<data_dir>/uploads` (query app uploads, paths `uploads/<file>`) |
| UI | Local web app (DAG graph, run launcher, runs with live progress, version/compare, document view with PDF highlights, side-by-side search, sources) |
| Query app | Separate `/chat` app for staff: Claude answers only from the library with citations; uploads join the library via single-doc runs |

## Tool choices

| Stage | Choice | Why | License |
| --- | --- | --- | --- |
| Parse / OCR | Docling standard pipeline, Apple Vision via ocrmac for image regions | Uses the PDF text layer for the ~80% born-digital pages (exact fees), OCR only where needed; tables and page/bbox provenance | MIT (Docling, ocrmac); Apache 2.0 / CDLA-Permissive models |
| Chunking | Docling HybridChunker, ~512 tokens on the embedding tokenizer | Structure-aware, tokenizer-aligned, repeats table headers | MIT |
| Embedding | Qwen3-Embedding-0.6B (pinned revision), MPS | Strongest small permissive model; fast on Apple Silicon | Apache 2.0 |
| Considered | PaddleOCR-VL-1.6 / OvisOCR2 (re-read every page from pixels, ignore the text layer), Qwen3-VL-Embedding, harrier-oss-v1 | Kept as swap-ins | Apache 2.0 / MIT |
| Excluded | Marker/Surya, Chandra 2, jina-v5, MinerU | Restricted, non-commercial or unverified licenses | — |
| Stack | Python 3.13 (uv), custom engine, SQLite catalog + content-addressed JSON/Parquet, FastAPI + React/TS (Vite, Tailwind, shadcn/ui, PDF.js), exact in-memory search | Small, inspectable, no services to run | — |
| Query model | Claude API (`claude-sonnet-5-5`) with search_result citations | Answer quality; only questions and retrieved passages leave the machine | Commercial terms |

## How it was built

- Shared interfaces first (contract types and typed stubs), then the repo's `parallel-implement` skill: one Opus 5.5 subagent per component on non-overlapping files.
- Phase 1 in dependency waves: (1) config/spec/fingerprints/git, catalog/objects, default nodes, logging → (2) sources, validation, publish/metrics → (3) engine and state → (4) CLI, multi-folder sources, catalog additions and library view → single-doc runs.
- Phases 2 and 3 ran in parallel in separate worktrees and sessions, coding against Phase 1 interfaces; Phase 2 was merged into `main` once Phase 1 was done.
- Commit attribution trailers were removed from history at your request.

## Changes made during implementation

- Per-doc cache key chains upstream fingerprints instead of input version ids, so adding a document only processes that document downstream.
- Outputs are stored by their own content hash behind a cache index, so a forced rerun never alters older versions.
- A node counts as uncommitted only when its own YAML entry, script or checks file changes.
- Identical reruns reuse the latest version; staleness propagates downstream; reading a scratch input makes a run scratch.
- CLI: a bare `dag/node` means the current version, and for `promote` the newest candidate.
- Revalidate records the checks fingerprint it used; the single-doc runner fails runs a previous runner left behind.
- API JSON lives under `/api`; the query app plugs in through a `router` and a `lifespan`.

## Verification

- 348 tests (16 need models) across the engine, CLI, store, sources, validation, publish, API and search; the fast suite passes on `main`. Phase 3 reports 104 passing on its branch.
- Fixture gate: 6 docs through all three nodes in 56 s; Szekeres fees ($1,750.00, $1,835.00) verbatim; unit-norm 1024-dim vectors; reruns fully cached.
- CLI walk-through: swap the chunker, scratch run on 2 docs, committed run reusing them, diff (86 → 148 chunks), promote chunk alone → embed stale.
- 25 real proposals: 188 pages (28% OCR'd), 487 chunks, 0 errors, 116 s.

## Open items

- Finish the full run (embed in progress) and promote it; the chat library goes live then.
- Two documents crashed the parser (native segfault); rerun them as a scratch run to reproduce.
- Retrieval quality: "aerial mapping fee" finds the right proposal but not its fee chunk first — tune chunking or the embedding header.
- Merge Phase 3 (`claude/phase-3-query-app-b67451`) into `main` when ready; the chat needs `ANTHROPIC_API_KEY` (or `ant auth login`) to answer.
- A test process from the Phase 3 session is still holding port 8765; the UIs run on 8766 (engineer) and 8767 (chat).
