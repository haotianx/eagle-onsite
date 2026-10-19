"""Embed chunk embed_text with a sentence-transformers model (Qwen3-Embedding) on Metal."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pyarrow as pa
import torch
from sentence_transformers import SentenceTransformer

from ingest.contract import Doc, NodeContext, only

ARTIFACT_TYPE = "embed"

DEFAULT_DIM = 1024
DEFAULT_QUERY_INSTRUCTION = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
)


def describe(params: dict) -> dict:
    return {
        "model": params["model"],
        "revision": params.get("revision"),
        "dim": int(params.get("dim", DEFAULT_DIM)),
        "metric": "cosine",
        "query_instruction": params.get("query_instruction", DEFAULT_QUERY_INSTRUCTION),
    }


def setup(params: dict) -> SentenceTransformer:
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(params["model"], revision=params.get("revision"), device=device)
    dim = model.get_embedding_dimension()
    expected = int(params.get("dim", DEFAULT_DIM))
    if dim != expected:
        raise ValueError(f"{params['model']} has dimension {dim}, params say {expected}")
    return model


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> pa.Table:
    chunks: pa.Table = only(inputs)
    model: SentenceTransformer = ctx.state
    dim = int(params.get("dim", DEFAULT_DIM))

    start = time.perf_counter()
    texts = chunks.column("embed_text").to_pylist()
    if texts:
        vectors = model.encode(
            texts,
            batch_size=int(params.get("batch_size", 32)),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    else:
        vectors = np.zeros((0, dim), dtype=np.float32)
    column = pa.FixedSizeListArray.from_arrays(pa.array(vectors.reshape(-1)), dim)
    ctx.log.info("embedded %d chunks in %.1fs", len(texts), time.perf_counter() - start)
    return chunks.append_column(pa.field("vector", pa.list_(pa.float32(), dim)), column)
