"""Seed an empty data dir with a realistic catalog for the engineer UI (dev and tests only).

Everything is written through the implemented Phase 1 APIs (Catalog, ObjectStore, sources.sync,
validation, obs, metrics, publish.write_published); only timestamps are rewritten afterwards so the
runs are spread over the last few days.

Contents, oldest first:
    snapshot 1  a copy of the fixtures (inside the data dir) without the Murphy PDF and with a
                truncated PDF added (flagged corrupt)
    snapshot 2  the `proposals` source (the fixtures): 6 docs from 7 files
    run A       whole DAG, committed: ocr/chunk/embed v1, promoted current (tables published)
    run B       embed, failed in setup (no version)
    run C       chunk with max_tokens 256 + downstream: chunk v2 and embed v2, promotable candidates
    run D       scratch ocr over 3 docs of snapshot 1, forced, working tree: ocr v2 with one failed
                doc (the corrupt PDF) and ctx.debug files
    run E       ocr forced over snapshot 2, cancelled after two docs (version abandoned)

real=True runs the default DAG's node scripts in-process (Docling, HybridChunker,
Qwen3-Embedding-0.6B; models must be cached locally). real=False writes synthetic records of the
same shapes (text from the PDF text layer, 16-dim bag-of-words vectors) in about a second.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import time
import traceback
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ingest import gitinfo, obs, sources, validation
from ingest.config import Config
from ingest.contract import Doc, NodeContext, make_chunk_ids, text_hash
from ingest.fingerprint import dag_fingerprints, env_hash, node_fingerprint, sha256_file
from ingest.metrics import doc_metrics
from ingest.publish import write_published
from ingest.spec import DagSpec, load_dags
from ingest.store.catalog import Catalog, VersionDocInput
from ingest.store.objects import ObjectStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "proposals"
DAG = "default"
SOURCE = "proposals"
EARLIER_DIR = "dev-sources/proposals-earlier"
"""Data-dir-relative folder synced as snapshot 1."""
DROPPED = "3649 Murphy Bella Vista/CONTRACT/Murphy - New Job Form & Signed Proposal.pdf"
TRUNCATED = "3002 ELLIOT/CONTRACT/Elliot Proposal - partial.pdf"
TRUNCATED_FROM = "3002 ELLIOT/CONTRACT/Mike Proposal_Elliot Pickle Ball.pdf"
CHUNK_VARIANT = {"max_tokens": 256}
SCRATCH_DOCS = ("2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf",
                "3426 AGUILAR COLINA FUERTE/CONTRACT/PROPOSAL ADDENDUM.pdf", TRUNCATED)
SYNTHETIC_DIM = 16


def seed(config: Config, *, real: bool = True) -> dict[str, Any]:
    """Populate config.data_dir (expected empty) for the default DAG over the `proposals`
    source. Returns {"snapshots": [...], "runs": {name: id}, "versions": {"node@vN": id}}."""
    if real:
        obs.configure_environment(offline=True)
    catalog = Catalog(config.catalog_path)
    try:
        return _Seeder(config, catalog, real).seed()
    finally:
        catalog.close()


# Timestamps


def _catalog_ts(t: datetime) -> str:
    return t.isoformat(timespec="microseconds")


def _log_ts(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _Backdate:
    """Rewrites timestamps the catalog set to "now" (separate connection, short transactions)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def set(self, table: str, key: Mapping[str, Any], **cols: datetime | None) -> None:
        sets = ", ".join(f"{c} = ?" for c in cols)
        where = " AND ".join(f"{k} = ?" for k in key)
        args = [None if v is None else _catalog_ts(v) for v in cols.values()] + list(key.values())
        self.many(f"UPDATE {table} SET {sets} WHERE {where}", [args])

    def many(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            with conn:
                conn.executemany(sql, rows)
        finally:
            conn.close()


# Node execution: real scripts or synthetic records


@dataclass
class _Result:
    record: Any = None
    duration_s: float = 0.0
    peak_rss_mb: float = 0.0
    lines: list[dict[str, Any]] = field(default_factory=list)
    error: BaseException | None = None
    traceback: str | None = None


class _RealNodes:
    """Runs the DAG's scripts in this process; setup() once per (node, params)."""

    def __init__(self, root: Path, dag: DagSpec) -> None:
        from ingest.scripts import load_script

        self.scripts = {n: load_script(root / s.script) for n, s in dag.nodes.items()}
        self._states: dict[tuple[str, str], Any] = {}

    def artifact_type(self, node: str) -> str | None:
        return self.scripts[node].artifact_type

    def describe(self, node: str, params: Mapping[str, Any]) -> dict[str, Any]:
        fn = self.scripts[node].describe
        return dict(fn(dict(params))) if fn else {}

    def setup(self, node: str, params: Mapping[str, Any]) -> tuple[Any, float]:
        key = (node, repr(sorted(params.items())))
        if key in self._states:
            return self._states[key], 0.0
        start = time.perf_counter()
        fn = self.scripts[node].setup
        state = fn(dict(params)) if fn else None
        self._states[key] = state
        return state, time.perf_counter() - start

    def run(self, node: str, doc: Doc, inputs: dict[str, Any], ctx: NodeContext, tags: Mapping[str, Any]) -> _Result:
        res = _Result()
        with obs.capture(node=node, **tags) as captured, obs.PeakRss() as rss:
            warnings.simplefilter("always")
            start = time.perf_counter()
            try:
                res.record = self.scripts[node].run(doc, inputs, dict(ctx.params), ctx)
            except Exception as e:
                res.error, res.traceback = e, traceback.format_exc()
            res.duration_s = time.perf_counter() - start
        res.peak_rss_mb = rss.peak_mb
        res.lines = captured.lines
        return res


class _SyntheticNodes:
    """Records of the built-in shapes without models: parse text comes from the PDF text layer."""

    LOAD_S = {"ocr": 6.8, "chunk": 1.1, "embed": 9.4}

    def __init__(self, root: Path, dag: DagSpec) -> None:
        self.dag = dag

    def artifact_type(self, node: str) -> str | None:
        return {"ocr": "parse", "chunk": "chunk", "embed": "embed"}.get(node)

    def describe(self, node: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if node == "embed":
            return {"model": "synthetic-bag-of-words", "revision": None, "dim": SYNTHETIC_DIM,
                    "metric": "cosine", "query_instruction": "Given a question, retrieve passages that answer it"}
        if node == "chunk":
            return {"chunker": "synthetic", "tokenizer": params.get("tokenizer"), "max_tokens": params.get("max_tokens")}
        return {"engine": "synthetic", "ocr_engine": params.get("ocr_engine")}

    def setup(self, node: str, params: Mapping[str, Any]) -> tuple[Any, float]:
        return None, self.LOAD_S.get(node, 1.0)

    def run(self, node: str, doc: Doc, inputs: dict[str, Any], ctx: NodeContext, tags: Mapping[str, Any]) -> _Result:
        res = _Result()
        try:
            res.record = {"ocr": _synthetic_parse, "chunk": _synthetic_chunk, "embed": _synthetic_embed}[node](doc, inputs, ctx.params)
        except Exception as e:
            res.error, res.traceback = e, traceback.format_exc()
            return res
        rows = 1 if isinstance(res.record, dict) else res.record.num_rows
        pages = len(res.record["pages"]) if node == "ocr" else 0
        res.duration_s = {"ocr": 1.4 * pages + 0.6, "chunk": 0.05 * rows + 0.2, "embed": 0.04 * rows + 0.3}[node]
        res.peak_rss_mb = {"ocr": 2150.0, "chunk": 610.0, "embed": 1830.0}[node] + 17.0 * rows
        line = {"ocr": f"parsed {pages} pages", "chunk": f"{rows} chunks", "embed": f"embedded {rows} chunks"}[node]
        res.lines = [{"level": "INFO", "kind": "log", "message": line, "logger": f"ingest.node.{node}", **tags, "node": node}]
        if node == "ocr" and any(p["ocr"] for p in res.record["pages"]):
            res.lines.append({"level": "WARNING", "kind": "stderr", "message": "OCR engine applied to bitmap regions",
                              **tags, "node": node})
        return res


def _synthetic_parse(doc: Doc, inputs: dict[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    import pypdfium2 as pdfium

    if doc.flags:
        raise ValueError(f"cannot parse {doc.paths[0]}: flagged {', '.join(doc.flags)}")
    pdf = pdfium.PdfDocument(doc.file)
    texts: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    try:
        for n, page in enumerate(pdf, start=1):
            width, height = (float(v) for v in page.get_size())
            raw = page.get_textpage().get_text_bounded()
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            paragraphs = [" ".join(lines[i : i + 6]) for i in range(0, len(lines), 6)]
            for i, para in enumerate(paragraphs):
                top = 72.0 + i * 64.0
                texts.append({
                    "self_ref": f"#/texts/{len(texts)}",
                    "label": "text",
                    "text": para,
                    "prov": [{"page_no": n, "charspan": [0, len(para)],
                              "bbox": {"l": 72.0, "t": height - top, "r": width - 72.0,
                                       "b": height - top - 56.0, "coord_origin": "BOTTOMLEFT"}}],
                })
            chars = sum(len(p) for p in paragraphs)
            pages.append({"page": n, "width": width, "height": height, "text_chars": chars, "ocr": chars < 20})
    finally:
        pdf.close()
    docling = {
        "schema_name": "DoclingDocument",
        "name": doc.file.stem,
        "texts": texts,
        "tables": [],
        "pages": {str(p["page"]): {"page_no": p["page"], "size": {"width": p["width"], "height": p["height"]}} for p in pages},
    }
    return {"docling": docling, "pages": pages}


def _chunk_schema() -> Any:
    import pyarrow as pa

    region = pa.struct([("page", pa.int64()), ("x0", pa.float64()), ("y0", pa.float64()), ("x1", pa.float64()), ("y1", pa.float64())])
    return pa.schema([
        ("chunk_id", pa.string()), ("doc_id", pa.string()), ("seq", pa.int64()), ("text", pa.string()),
        ("embed_text", pa.string()), ("pages", pa.list_(pa.int64())), ("section", pa.list_(pa.string())),
        ("regions", pa.list_(region)), ("text_hash", pa.string()), ("tokens", pa.int64()),
    ])


def _synthetic_chunk(doc: Doc, inputs: dict[str, Any], params: Mapping[str, Any]) -> Any:
    import pyarrow as pa

    record = inputs["ocr"]
    heights = {p["page"]: p["height"] for p in record["pages"]}
    budget = int(params["max_tokens"]) * 0.6
    header = f"Document: {doc.meta.get('filename') or Path(doc.paths[0]).name}\nPath: {doc.paths[0]}"
    groups: list[list[dict[str, Any]]] = [[]]
    for item in record["docling"]["texts"]:
        if groups[-1] and sum(len(t["text"].split()) for t in groups[-1]) + len(item["text"].split()) > budget:
            groups.append([])
        groups[-1].append(item)
    rows: dict[str, list[Any]] = {k: [] for k in _chunk_schema().names}
    for seq, group in enumerate(g for g in groups if g):
        text = "\n".join(t["text"] for t in group)
        regions = []
        for t in group:
            for prov in t["prov"]:
                b, h = prov["bbox"], heights[prov["page_no"]]
                regions.append({"page": prov["page_no"], "x0": b["l"], "y0": h - b["t"], "x1": b["r"], "y1": h - b["b"]})
        embed_text = f"{header}\n{text}"
        rows["seq"].append(seq)
        rows["text"].append(text)
        rows["embed_text"].append(embed_text)
        rows["pages"].append(sorted({r["page"] for r in regions}))
        rows["section"].append([])
        rows["regions"].append(regions)
        rows["text_hash"].append(text_hash(text))
        rows["tokens"].append(len(embed_text.split()))
    rows["chunk_id"] = make_chunk_ids(doc.id, rows["text"])
    rows["doc_id"] = [doc.id] * len(rows["text"])
    return pa.table(rows, schema=_chunk_schema())


def _synthetic_embed(doc: Doc, inputs: dict[str, Any], params: Mapping[str, Any]) -> Any:
    import numpy as np
    import pyarrow as pa

    chunks = inputs["chunk"]
    vectors = np.zeros((chunks.num_rows, SYNTHETIC_DIM), dtype=np.float32)
    for i, text in enumerate(chunks.column("embed_text").to_pylist()):
        for token in re.findall(r"[a-z0-9$.,]+", text.lower()):
            vectors[i, int(hashlib.sha1(token.encode()).hexdigest()[:8], 16) % SYNTHETIC_DIM] += 1.0
        vectors[i, 0] += 1e-3
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-12)
    column = pa.FixedSizeListArray.from_arrays(pa.array(vectors.reshape(-1)), SYNTHETIC_DIM)
    return chunks.append_column(pa.field("vector", pa.list_(pa.float32(), SYNTHETIC_DIM)), column)


# Runs


class _Run:
    """One run: catalog row and events, log.jsonl, metrics, on a simulated clock."""

    def __init__(self, seeder: _Seeder, request: dict[str, Any], *, start: datetime, scratch: bool, dirty: bool) -> None:
        self.s = seeder
        self.clock = start
        self.created = start
        self.started: datetime | None = None
        self.id = seeder.catalog.create_run(DAG, request, scratch=scratch, commit=seeder.commit, dirty=dirty)
        self.log = obs.RunLog(seeder.config.run_dir(self.id), run_id=self.id, dag=DAG)
        self._event_ts: list[tuple[str, int]] = []
        self.versions: list[tuple[int, datetime, datetime]] = []
        self.event("run_queued", {}, message="queued")

    def advance(self, seconds: float) -> None:
        self.clock += timedelta(seconds=seconds)

    def tags(self, **extra: Any) -> dict[str, Any]:
        return {"run_id": self.id, "dag": DAG, **{k: v for k, v in extra.items() if v is not None}}

    def event(self, kind: str, payload: Mapping[str, Any], *, level: str = "INFO", message: str = "",
              node: str | None = None, version_id: int | None = None, doc_id: str | None = None) -> None:
        event_id = self.s.catalog.add_event(self.id, kind, payload)
        self._event_ts.append((_catalog_ts(self.clock), event_id))
        self.log.write_lines([{"ts": _log_ts(self.clock), "level": level, "kind": "event", "event": kind,
                               "message": message or kind.replace("_", " "),
                               **self.tags(node=node, version_id=version_id, doc_id=doc_id), **payload}])

    def captured(self, lines: Sequence[dict[str, Any]], start: datetime, duration_s: float) -> None:
        n = len(lines)
        out = [{**ln, "ts": _log_ts(start + timedelta(seconds=duration_s * (i + 1) / (n + 1)))} for i, ln in enumerate(lines)]
        self.log.write_lines(out)

    def start(self, payload: Mapping[str, Any]) -> None:
        self.advance(1.2)
        self.started = self.clock
        self.s.catalog.set_run_status(self.id, "running")
        self.event("run_started", payload)

    def finish(self, status: str, error: str | None = None) -> None:
        self.advance(0.4)
        if status == "cancelled":
            self.s.catalog.request_cancel(self.id)
        self.event("run_finished", {"status": status, **({"error": error} if error else {})},
                   level="ERROR" if status == "failed" else "INFO")
        self.s.catalog.set_run_status(self.id, status, error)  # type: ignore[arg-type]
        self.log.close()
        b = self.s.backdate
        b.set("runs", {"id": self.id}, created_at=self.created, started_at=self.started, ended_at=self.clock)
        b.many("UPDATE run_events SET ts = ? WHERE id = ?", self._event_ts)
        for version_id, created, finalized in self.versions:
            b.set("versions", {"id": version_id}, created_at=created, finalized_at=finalized)


@dataclass(frozen=True)
class _Built:
    version_id: int
    seq: int
    records: dict[str, Any]
    """doc id → record for ok docs."""
    docs: list[VersionDocInput]


class _Seeder:
    def __init__(self, config: Config, catalog: Catalog, real: bool) -> None:
        self.config = config
        self.catalog = catalog
        self.objects = ObjectStore(config.objects_dir)
        self.backdate = _Backdate(config.catalog_path)
        root = config.repo_root
        self.dag = load_dags(root, config.sources)[DAG]
        self.source = config.sources[self.dag.source]
        self.env = env_hash(root)
        self.fps = dag_fingerprints(self.dag, root, self.env)
        self.commit = gitinfo.head_commit(root)
        self.nodes = (_RealNodes if real else _SyntheticNodes)(root, self.dag)
        self.now = datetime.now(UTC).replace(microsecond=0)

    # Snapshots

    def snapshots(self) -> tuple[int, int, list[Doc], list[Doc]]:
        earlier = self.config.data_dir / EARLIER_DIR
        shutil.copytree(self.source.root, earlier, ignore=shutil.ignore_patterns(".*"))
        (earlier / DROPPED).unlink()
        data = (self.source.root / TRUNCATED_FROM).read_bytes()
        (earlier / TRUNCATED).write_bytes(data[: len(data) // 2])
        earlier_source = replace(self.source, root=earlier)
        s1 = sources.sync(self.catalog, earlier_source).snapshot_id
        s2 = sources.sync(self.catalog, self.source).snapshot_id
        self.backdate.set("snapshots", {"id": s1}, created_at=self.now - timedelta(days=4, hours=3))
        self.backdate.set("snapshots", {"id": s2}, created_at=self.now - timedelta(days=3, hours=2))
        return s1, s2, sources.snapshot_docs(self.catalog, s1, earlier_source), sources.snapshot_docs(self.catalog, s2, self.source)

    # Versions

    def build(self, run: _Run, node: str, *, params: Mapping[str, Any], fp: str, snapshot_id: int, docs: Sequence[Doc],
              inputs: Mapping[str, _Built], scratch: bool, dirty: bool, debug: bool = False,
              script_issues: Sequence[tuple[int, str, str]] = ()) -> _Built:
        spec = self.dag.nodes[node]
        atype = self.nodes.artifact_type(node)
        describe = self.nodes.describe(node, params)
        meta = {"script": spec.script, "script_hash": sha256_file(self.config.repo_root / spec.script),
                "params": dict(params), "env": self.env, "describe": describe, "checks_fp": None}
        v = self.catalog.create_version(
            dag=DAG, node=node, node_fp=fp, snapshot_id=snapshot_id,
            input_versions={i: b.version_id for i, b in inputs.items()}, run_id=run.id, commit=self.commit,
            dirty=dirty, scratch=scratch, artifact_type=atype, meta=meta)
        created = run.clock
        run.event("node_started", {"node": node, "version_id": v.id}, node=node, version_id=v.id)
        state, load_s = self.nodes.setup(node, params)
        run.advance(load_s)
        node_start = run.clock
        run_dir = self.config.run_dir(run.id)

        rows: list[VersionDocInput] = []
        records: dict[str, Any] = {}
        hard: list[tuple[str | None, Any]] = []
        quality: list[tuple[str | None, Any]] = []
        doc_metric_rows: list[tuple[str | None, str | None, str, float]] = []
        for doc in docs:
            upstream = {i: b.records.get(doc.id) for i, b in inputs.items()}
            if any(r is None for r in upstream.values()):
                rows.append(VersionDocInput(doc.id, "skipped"))
                run.event("doc_skipped", {"node": node, "doc_id": doc.id, "reason": "input failed"}, node=node,
                          version_id=v.id, doc_id=doc.id)
                continue
            ctx = NodeContext(dag=DAG, node=node, params=params, state=state, scratch=scratch,
                              debug_sink=obs.debug_sink(run_dir, node, doc.id) if scratch else None)
            tags = run.tags(version_id=v.id, doc_id=doc.id)
            res = self.nodes.run(node, doc, upstream, ctx, {k: tags.get(k) for k in ("run_id", "dag", "version_id", "doc_id")})
            doc_start = run.clock
            run.advance(max(res.duration_s, 0.05))
            run.captured(res.lines, doc_start, res.duration_s)
            doc_metric_rows += [(node, doc.id, "duration_s", res.duration_s), (node, doc.id, "peak_rss_mb", res.peak_rss_mb)]
            if res.error is not None:
                rows.append(VersionDocInput(doc.id, "error", error_type=type(res.error).__name__, error_message=str(res.error),
                                            traceback=res.traceback, duration_s=res.duration_s,
                                            peak_rss_mb=res.peak_rss_mb, worker_pid=40000 + run.id))
                run.event("doc_failed", {"node": node, "doc_id": doc.id, "error_type": type(res.error).__name__,
                                         "message": str(res.error), "duration_s": res.duration_s},
                          level="ERROR", message=f"{type(res.error).__name__}: {res.error}", node=node,
                          version_id=v.id, doc_id=doc.id)
                run.log.write_lines([{"ts": _log_ts(run.clock), "level": "ERROR", "kind": "log", "message": res.traceback,
                                      **run.tags(node=node, version_id=v.id, doc_id=doc.id)}])
                continue
            record = res.record
            sha, kind = self.objects.put(record)
            m = doc_metrics(atype, record)  # type: ignore[arg-type]
            self.catalog.cache_put(fp, doc.id, sha, kind, res.duration_s, m)
            doc_hard = validation.hard_checks(atype, record, params, inputs=upstream, meta=describe)  # type: ignore[arg-type]
            hard += [(doc.id, i) for i in doc_hard]
            quality += [(doc.id, i) for i in validation.quality_checks(atype, record, params)]  # type: ignore[arg-type]
            if debug and isinstance(record, dict):
                ctx.debug("pages.json", record.get("pages") or [])
                ctx.debug("summary.txt", f"{doc.paths[0]}\n{len(record.get('pages') or [])} pages\n")
            status = "error" if any(i.level == "error" for i in doc_hard) else "ok"
            rows.append(VersionDocInput(doc.id, status, object_sha=sha, kind=kind, duration_s=res.duration_s,
                                        peak_rss_mb=res.peak_rss_mb, worker_pid=40000 + run.id, metrics=m,
                                        **({"error_type": "HardCheckError", "error_message": doc_hard[0].message}
                                           if status == "error" else {})))
            if status == "ok":
                records[doc.id] = record
            run.event("doc_finished", {"node": node, "doc_id": doc.id, "duration_s": res.duration_s,
                                       "records": int(m["records"])}, node=node, version_id=v.id, doc_id=doc.id)

        self.catalog.add_version_docs(v.id, rows)
        if hard:
            self.catalog.add_issues(v.id, "hard", hard)
        if quality:
            self.catalog.add_issues(v.id, "quality", quality)
        if script_issues:
            from ingest.contract import Issue

            ok_ids = [r.doc_id for r in rows if r.status == "ok"]
            self.catalog.add_issues(v.id, "script", [(ok_ids[i % len(ok_ids)], Issue("warning", code, msg))
                                                     for i, code, msg in script_issues])
        wall = (run.clock - node_start).total_seconds()
        stats = self.stats(rows, wall)
        final = self.catalog.finalize_version(v.id, stats)
        run.versions.append((v.id, created, run.clock))
        ok, err, skipped = stats["ok"], stats["error"], stats["skipped"]
        run.event("node_finished", {"node": node, "version_id": v.id, "version": final.label, "processed": stats["processed"],
                                    "cached": 0, "ok": ok, "error": err, "skipped": skipped, "wall_s": wall},
                  node=node, version_id=v.id)
        self.node_metrics(run, node, rows, wall, load_s, doc_metric_rows)
        return _Built(v.id, final.seq or 0, records, rows)

    def stats(self, rows: Sequence[VersionDocInput], wall: float) -> dict[str, Any]:
        ok = [r for r in rows if r.status == "ok"]
        totals: dict[str, float] = {}
        for r in ok:
            for k, val in r.metrics.items():
                if k != "tokens_mean":
                    totals[k] = totals.get(k, 0.0) + val
        return {
            "docs": len(rows),
            "ok": len(ok),
            "error": sum(r.status == "error" for r in rows),
            "skipped": sum(r.status == "skipped" for r in rows),
            "cached": sum(r.cached for r in rows),
            "processed": sum(r.status != "skipped" and not r.cached for r in rows),
            "size_bytes": sum(self.objects.size(r.object_sha, r.kind) for r in ok),  # type: ignore[arg-type]
            "wall_s": wall,
            "totals": totals,
        }

    def node_metrics(self, run: _Run, node: str, rows: Sequence[VersionDocInput], wall: float, load_s: float,
                     per_doc: list[tuple[str | None, str | None, str, float]]) -> None:
        n = len(rows) or 1
        failed = sum(r.status == "error" for r in rows)
        skipped = sum(r.status == "skipped" for r in rows)
        cached = sum(r.cached for r in rows)
        metrics = {
            "wall_s": wall, "docs_processed": len(rows) - skipped - cached, "docs_cached": cached,
            "docs_failed": failed, "docs_skipped": skipped, "cache_hit_rate": cached / n, "failure_rate": failed / n,
            "model_load_s": load_s,
        }
        if self.nodes.artifact_type(node) == "parse":
            pages = sum(r.metrics.get("pages", 0) for r in rows)
            metrics |= {"pages": pages, "ocr_pages": sum(r.metrics.get("ocr_pages", 0) for r in rows),
                        "pages_per_s": pages / wall if wall else 0.0}
        self.catalog.add_metrics(run.id, [(node, None, k, v) for k, v in metrics.items()] + per_doc)

    def request(self, **fields: Any) -> dict[str, Any]:
        base = {"dag": DAG, "target": None, "selection": "all", "snapshot_id": None, "input_versions": {},
                "recompute": "reuse", "docs": "all", "code": "committed", "sample_seed": 0}
        return base | fields

    def promote(self, built: _Built, node: str, at: datetime) -> None:
        self.catalog.set_current(DAG, node, built.version_id)
        self.backdate.set("current", {"dag": DAG, "node": node}, promoted_at=at)
        if self.nodes.artifact_type(node) in ("chunk", "embed"):
            write_published(self.catalog, self.objects, built.version_id, self.config.published_dir)

    # Scenario

    def seed(self) -> dict[str, Any]:
        s1, s2, docs1, docs2 = self.snapshots()
        params = {n: dict(self.dag.nodes[n].params) for n in self.dag.nodes}
        all_nodes = self.dag.topo_order()
        runs: dict[str, int] = {}
        versions: dict[str, int] = {}

        # A: full run, promoted
        run = _Run(self, self.request(snapshot_id=s2, nodes=all_nodes), start=self.now - timedelta(days=3), scratch=False, dirty=False)
        run.start({"docs": len(docs2), "nodes": all_nodes, "snapshot_id": s2, "scratch": False, "code": "committed"})
        ocr1 = self.build(run, "ocr", params=params["ocr"], fp=self.fps["ocr"], snapshot_id=s2, docs=docs2, inputs={}, scratch=False, dirty=False)
        chunk1 = self.build(run, "chunk", params=params["chunk"], fp=self.fps["chunk"], snapshot_id=s2, docs=docs2,
                            inputs={"ocr": ocr1}, scratch=False, dirty=False)
        embed1 = self.build(run, "embed", params=params["embed"], fp=self.fps["embed"], snapshot_id=s2, docs=docs2,
                            inputs={"chunk": chunk1}, scratch=False, dirty=False)
        run.finish("succeeded")
        promoted_at = run.clock + timedelta(minutes=25)
        for node, built in (("ocr", ocr1), ("chunk", chunk1), ("embed", embed1)):
            self.promote(built, node, promoted_at)
            versions[f"{node}@v{built.seq}"] = built.version_id
        runs["full"] = run.id

        # B: embed fails in setup
        run = _Run(self, self.request(target="embed", selection="node", snapshot_id=s2, code="working", nodes=["embed"]),
                   start=self.now - timedelta(days=2, hours=4), scratch=False, dirty=True)
        run.start({"docs": len(docs2), "nodes": ["embed"], "snapshot_id": s2, "scratch": False, "code": "working"})
        v = self.catalog.create_version(dag=DAG, node="embed", node_fp=self.fps["embed"], snapshot_id=s2,
                                        input_versions={"chunk": chunk1.version_id}, run_id=run.id, commit=self.commit,
                                        dirty=True, scratch=False, artifact_type="embed",
                                        meta={"script": self.dag.nodes["embed"].script, "params": params["embed"], "env": self.env})
        run.event("node_started", {"node": "embed", "version_id": v.id}, node="embed", version_id=v.id)
        run.advance(3.7)
        error = "embed: setup failed"
        try:
            _load_offline_model(params["embed"]["model"])
        except OSError as e:
            error = f"embed: setup failed: {type(e).__name__}: {e}"
            run.log.write_lines([{"ts": _log_ts(run.clock), "level": "ERROR", "kind": "log", "message": traceback.format_exc(),
                                  **run.tags(node="embed", version_id=v.id)}])
        self.catalog.abandon_version(v.id)
        run.finish("failed", error)
        runs["failed"] = run.id

        # C: chunk variant + downstream → candidates
        chunk_params = params["chunk"] | CHUNK_VARIANT
        chunk_fp = node_fingerprint(sha256_file(self.config.repo_root / self.dag.nodes["chunk"].script), chunk_params,
                                    self.env, {"ocr": self.fps["ocr"]})
        embed_fp = node_fingerprint(sha256_file(self.config.repo_root / self.dag.nodes["embed"].script), params["embed"],
                                    self.env, {"chunk": chunk_fp})
        run = _Run(self, self.request(target="chunk", selection="downstream", snapshot_id=s2, nodes=["chunk", "embed"]),
                   start=self.now - timedelta(days=1, hours=2), scratch=False, dirty=False)
        run.start({"docs": len(docs2), "nodes": ["chunk", "embed"], "snapshot_id": s2, "scratch": False, "code": "committed"})
        chunk2 = self.build(run, "chunk", params=chunk_params, fp=chunk_fp, snapshot_id=s2, docs=docs2, inputs={"ocr": ocr1},
                            scratch=False, dirty=False, script_issues=[
                                (0, "short_chunk", "chunk 3 has fewer than 20 tokens"),
                                (1, "section_missing", "2 chunks have no section heading")])
        embed2 = self.build(run, "embed", params=params["embed"], fp=embed_fp, snapshot_id=s2, docs=docs2,
                            inputs={"chunk": chunk2}, scratch=False, dirty=False)
        run.finish("succeeded")
        versions[f"chunk@v{chunk2.seq}"] = chunk2.version_id
        versions[f"embed@v{embed2.seq}"] = embed2.version_id
        runs["candidate"] = run.id

        # D: scratch ocr over three docs of snapshot 1, one corrupt
        by_path = {d.paths[0]: d for d in docs1}
        picked = [by_path[p] for p in SCRATCH_DOCS]
        run = _Run(self, self.request(target="ocr", selection="node", snapshot_id=s1, recompute="force",
                                      docs=[d.id for d in picked], code="working", nodes=["ocr"]),
                   start=self.now - timedelta(hours=5), scratch=True, dirty=True)
        run.start({"docs": len(picked), "nodes": ["ocr"], "snapshot_id": s1, "scratch": True, "code": "working"})
        ocr2 = self.build(run, "ocr", params=params["ocr"], fp=self.fps["ocr"], snapshot_id=s1, docs=picked, inputs={},
                          scratch=True, dirty=True, debug=True)
        run.finish("succeeded")
        versions[f"ocr@v{ocr2.seq}"] = ocr2.version_id
        runs["scratch"] = run.id

        # E: forced ocr over snapshot 2, cancelled after two docs
        run = _Run(self, self.request(target="ocr", selection="node", snapshot_id=s2, recompute="force", nodes=["ocr"]),
                   start=self.now - timedelta(hours=1), scratch=False, dirty=False)
        run.start({"docs": len(docs2), "nodes": ["ocr"], "snapshot_id": s2, "scratch": False, "code": "committed"})
        v = self.catalog.create_version(dag=DAG, node="ocr", node_fp=self.fps["ocr"], snapshot_id=s2, input_versions={},
                                        run_id=run.id, commit=self.commit, dirty=False, scratch=False, artifact_type="parse",
                                        meta={"script": self.dag.nodes["ocr"].script, "params": params["ocr"], "env": self.env})
        run.event("node_started", {"node": "ocr", "version_id": v.id}, node="ocr", version_id=v.id)
        run.advance(_SyntheticNodes.LOAD_S["ocr"])
        for row in ocr1.docs[:2]:
            run.advance(row.duration_s or 1.0)
            run.event("doc_finished", {"node": "ocr", "doc_id": row.doc_id, "duration_s": row.duration_s, "records": 1},
                      node="ocr", version_id=v.id, doc_id=row.doc_id)
        self.catalog.abandon_version(v.id)
        run.finish("cancelled")
        runs["cancelled"] = run.id

        return {"snapshots": [s1, s2], "runs": runs, "versions": versions}


def _load_offline_model(model: str) -> None:
    raise OSError(
        f"We couldn't connect to 'https://huggingface.co' to load the files, and couldn't find them in the cached files."
        f" Check your connection or see how to run the library in offline mode for {model}."
    )
