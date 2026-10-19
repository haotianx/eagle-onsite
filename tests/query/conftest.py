"""Shared query-app fixtures: a synthetic library (publish.Library) whose vectors come from a
deterministic bag-of-words embedder, so searches rank predictably without loading a model."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa
import pytest

from ingest.contract import make_chunk_ids, text_hash
from ingest.publish import SCHEMA_META_KEY, Library

DIM = 64
BOILERPLATE = "Payment is due within thirty days of the invoice date. Late payments accrue interest."


def bow_vector(text: str) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    for token in re.findall(r"[a-z0-9$.,]+", text.lower()):
        v[int(hashlib.sha1(token.encode()).hexdigest()[:8], 16) % DIM] += 1.0
    if not v.any():
        v[0] = 1.0
    return v / np.linalg.norm(v)


class FakeEmbedder:
    def embed(self, query: str) -> np.ndarray:
        return bow_vector(query)


@dataclass
class FakeDoc:
    doc_id: str
    path: str
    job_number: str | None
    project: str | None
    subfolder: str | None
    chunks: Sequence[tuple[str, Sequence[int], Sequence[str]]]
    """(text, pages, section path)"""
    pages: int = 2
    upload: bool = False
    pending: bool = False
    filename: str = field(init=False)

    def __post_init__(self) -> None:
        self.filename = self.path.rsplit("/", 1)[-1]


def default_docs() -> list[FakeDoc]:
    return [
        FakeDoc(
            "a" * 64,
            "2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf",
            "2292",
            "SZEKERES",
            "CONTRACT/AEROTECH",
            [
                ("Scope of work: aerial mapping and topographic survey of the site.", [1], ["Scope"]),
                ("Aerial mapping fee $1,750.00. Topographic survey fee $1,835.00.", [1], ["Fees"]),
                (BOILERPLATE, [2], ["Terms"]),
            ],
        ),
        FakeDoc(
            "b" * 64,
            "3002 ELLIOT/CONTRACT/Mike Proposal_Elliot Pickle Ball.pdf",
            "3002",
            "ELLIOT",
            "CONTRACT",
            [
                ("Pickle ball court grading plan and drainage design.", [1], ["Scope"]),
                ("Civil engineering services for the pickle ball courts.", [2, 3], ["Scope", "Civil"]),
                (BOILERPLATE, [9], ["Terms"]),
            ],
            pages=9,
        ),
        FakeDoc(
            "c" * 64,
            "3426 AGUILAR COLINA FUERTE/CONTRACT/PROPOSAL ADDENDUM.pdf",
            "3426",
            "AGUILAR COLINA FUERTE",
            "CONTRACT",
            [
                ("Addendum: additional staking visits billed hourly.", [1], ["Addendum"]),
                (BOILERPLATE, [1], ["Addendum"]),
            ],
            pages=1,
        ),
        FakeDoc(
            "d" * 64,
            "uploads/Retainer Letter.pdf",
            None,
            None,
            None,
            [("Retainer of $5,000.00 is required before work begins.", [1], [])],
            pages=1,
            upload=True,
            pending=True,
        ),
    ]


def version_meta(version_id: int = 1, seq: int = 1) -> dict:
    return {
        "dag": "default",
        "node": "embed",
        "version": f"default/embed@v{seq}",
        "seq": seq,
        "version_id": version_id,
        "model": "fake-embedder",
        "revision": None,
        "dim": DIM,
        "metric": "cosine",
        "query_instruction": "Query:",
    }


def make_library(docs: Sequence[FakeDoc] | None = None, *, version_id: int = 1, seq: int = 1) -> Library:
    """A Library shaped like publish.library's: embed columns + metadata columns + dup_count."""
    docs = default_docs() if docs is None else list(docs)
    rows: list[dict] = []
    for doc in docs:
        texts = [c[0] for c in doc.chunks]
        for seq_i, (chunk_id, (text, pages, section)) in enumerate(zip(make_chunk_ids(doc.doc_id, texts), doc.chunks)):
            rows.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": doc.doc_id,
                    "seq": seq_i,
                    "text": text,
                    "embed_text": f"{doc.filename}\n{' > '.join(section)}\n{text}",
                    "pages": list(pages),
                    "section": list(section),
                    "regions": [{"page": p, "x0": 72.0, "y0": 100.0 + 20 * seq_i, "x1": 540.0, "y1": 160.0 + 20 * seq_i} for p in pages],
                    "text_hash": text_hash(text),
                    "tokens": len(text.split()),
                    "vector": bow_vector(text).tolist(),
                    "paths": [doc.path],
                    "job_number": doc.job_number,
                    "project": doc.project,
                    "subfolder": doc.subfolder,
                    "filename": doc.filename,
                }
            )
    region = pa.struct([("page", pa.int64()), ("x0", pa.float64()), ("y0", pa.float64()), ("x1", pa.float64()), ("y1", pa.float64())])
    schema = pa.schema(
        [
            ("chunk_id", pa.string()),
            ("doc_id", pa.string()),
            ("seq", pa.int64()),
            ("text", pa.string()),
            ("embed_text", pa.string()),
            ("pages", pa.list_(pa.int64())),
            ("section", pa.list_(pa.string())),
            ("regions", pa.list_(region)),
            ("text_hash", pa.string()),
            ("tokens", pa.int64()),
            ("vector", pa.list_(pa.float32(), DIM)),
            ("paths", pa.list_(pa.string())),
            ("job_number", pa.string()),
            ("project", pa.string()),
            ("subfolder", pa.string()),
            ("filename", pa.string()),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    docs_per_hash = Counter()
    for h in {(r["text_hash"], r["doc_id"]) for r in rows}:
        docs_per_hash[h[0]] += 1
    table = table.append_column("dup_count", pa.array([docs_per_hash[r["text_hash"]] for r in rows], pa.int64()))
    meta = version_meta(version_id, seq)
    table = table.replace_schema_metadata({SCHEMA_META_KEY: json.dumps(meta).encode()})
    documents = pa.Table.from_pylist(
        [
            {
                "doc_id": d.doc_id,
                "paths": [d.path],
                "job_number": d.job_number,
                "project": d.project,
                "subfolder": d.subfolder,
                "filename": d.filename,
                "pages": d.pages,
                "upload": d.upload,
                "pending": d.pending,
            }
            for d in docs
        ],
        schema=pa.schema(
            [
                ("doc_id", pa.string()),
                ("paths", pa.list_(pa.string())),
                ("job_number", pa.string()),
                ("project", pa.string()),
                ("subfolder", pa.string()),
                ("filename", pa.string()),
                ("pages", pa.int64()),
                ("upload", pa.bool_()),
                ("pending", pa.bool_()),
            ]
        ),
    )
    return Library(rows=table, documents=documents, version=meta)


@pytest.fixture
def fake_docs() -> list[FakeDoc]:
    return default_docs()


@pytest.fixture
def library() -> Library:
    return make_library()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def library_factory():
    """make_library(docs=None, *, version_id=1, seq=1); docs are FakeDoc (see fake_doc_type)."""
    return make_library


@pytest.fixture
def fake_doc_type() -> type[FakeDoc]:
    return FakeDoc
