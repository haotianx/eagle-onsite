"""Embed-shaped rows for the query integration tests: one chunk per page with text, hashed
bag-of-words vectors (no model)."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import numpy as np
import pyarrow as pa

from ingest.contract import Doc, NodeContext, make_chunk_ids, only, text_hash

DIM = 16
_REGION = pa.struct([("page", pa.int64()), *((k, pa.float64()) for k in ("x0", "y0", "x1", "y1"))])


def vector(text: str) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    for token in re.findall(r"[a-z0-9$]+", text.lower()):
        h = hashlib.sha1(token.encode()).digest()
        v[h[0] % DIM] += 1.0
    n = float(np.linalg.norm(v))
    return v / n if n else v


def describe(params: dict) -> dict:
    return {"model": params["model"], "revision": None, "dim": DIM, "metric": "cosine", "query_instruction": ""}


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> pa.Table:
    pages = [p for p in only(inputs)["pages"] if p["text"].strip()]
    texts = [" ".join(p["text"].split()) for p in pages]
    vectors = np.stack([vector(t) for t in texts]) if texts else np.zeros((0, DIM), dtype=np.float32)
    return pa.table(
        {
            "chunk_id": make_chunk_ids(doc.id, texts),
            "doc_id": [doc.id] * len(texts),
            "seq": pa.array(range(len(texts)), pa.int64()),
            "text": texts,
            "embed_text": texts,
            "pages": pa.array([[p["page"]] for p in pages], pa.list_(pa.int64())),
            "section": pa.array([[] for _ in texts], pa.list_(pa.string())),
            "regions": pa.array([[] for _ in texts], pa.list_(_REGION)),
            "text_hash": [text_hash(t) for t in texts],
            "tokens": pa.array([len(t.split()) for t in texts], pa.int64()),
            "vector": pa.FixedSizeListArray.from_arrays(pa.array(vectors.ravel(), pa.float32()), DIM),
        }
    )
