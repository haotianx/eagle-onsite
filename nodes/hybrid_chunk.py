"""Chunk a Docling document with HybridChunker on the embedding model's tokenizer."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import PurePosixPath
from typing import Any

import pyarrow as pa
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.types.doc import CoordOrigin, DoclingDocument
from transformers import AutoTokenizer, PreTrainedTokenizerBase

from ingest.contract import CHUNK_COLUMNS, Doc, NodeContext, make_chunk_ids, only, text_hash

ARTIFACT_TYPE = "chunk"

REGION = pa.struct(
    [
        ("page", pa.int32()),
        ("x0", pa.float64()),
        ("y0", pa.float64()),
        ("x1", pa.float64()),
        ("y1", pa.float64()),
    ]
)
SCHEMA = pa.schema(
    [
        ("chunk_id", pa.string()),
        ("doc_id", pa.string()),
        ("seq", pa.int32()),
        ("text", pa.string()),
        ("embed_text", pa.string()),
        ("pages", pa.list_(pa.int32())),
        ("section", pa.list_(pa.string())),
        ("regions", pa.list_(REGION)),
        ("text_hash", pa.string()),
        ("tokens", pa.int32()),
    ]
)
assert tuple(SCHEMA.names) == CHUNK_COLUMNS

JOIN_SLACK = 2
"""Tokens reserved for merging at the header/body boundary."""


def describe(params: dict) -> dict:
    return {
        "chunker": "docling.HybridChunker",
        "docling_core": version("docling-core"),
        "tokenizer": params["tokenizer"],
        "tokenizer_revision": params.get("tokenizer_revision"),
        "max_tokens": params["max_tokens"],
    }


def setup(params: dict) -> PreTrainedTokenizerBase:
    return AutoTokenizer.from_pretrained(
        params["tokenizer"], revision=params.get("tokenizer_revision")
    )


def _header(doc: Doc) -> str:
    path = doc.paths[0]
    filename = doc.meta.get("filename") or PurePosixPath(path).name
    return f"Document: {filename}\nPath: {path}"


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> pa.Table:
    record = only(inputs)
    document = DoclingDocument.model_validate(record["docling"])
    heights = {p["page"]: float(p["height"]) for p in record["pages"]}
    hf_tokenizer: PreTrainedTokenizerBase = ctx.state
    max_tokens = int(params["max_tokens"])

    header = _header(doc)
    budget = max_tokens - len(hf_tokenizer.tokenize(header)) - JOIN_SLACK
    if budget <= 0:
        raise ValueError(f"header leaves no token budget within max_tokens={max_tokens}")
    tokenizer = HuggingFaceTokenizer(tokenizer=hf_tokenizer, max_tokens=budget)
    chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)

    rows: dict[str, list[Any]] = {name: [] for name in CHUNK_COLUMNS}
    for seq, chunk in enumerate(chunker.chunk(document)):
        embed_text = f"{header}\n{chunker.contextualize(chunk)}"
        regions = []
        for item in chunk.meta.doc_items:
            for prov in item.prov:
                height = heights.get(prov.page_no) or document.pages[prov.page_no].size.height
                box = prov.bbox
                if box.coord_origin != CoordOrigin.TOPLEFT:
                    box = box.to_top_left_origin(page_height=height)
                region = {
                    "page": prov.page_no,
                    "x0": float(box.l),
                    "y0": float(box.t),
                    "x1": float(box.r),
                    "y1": float(box.b),
                }
                if region not in regions:
                    regions.append(region)
        rows["seq"].append(seq)
        rows["text"].append(chunk.text)
        rows["embed_text"].append(embed_text)
        rows["pages"].append(sorted({r["page"] for r in regions}))
        rows["section"].append(list(chunk.meta.headings or []))
        rows["regions"].append(regions)
        rows["text_hash"].append(text_hash(chunk.text))
        rows["tokens"].append(len(hf_tokenizer.tokenize(embed_text)))

    rows["chunk_id"] = make_chunk_ids(doc.id, rows["text"])
    rows["doc_id"] = [doc.id] * len(rows["text"])
    table = pa.table(rows, schema=SCHEMA)

    over = sum(t > max_tokens for t in rows["tokens"])
    if over:
        ctx.log.warning("%d chunks exceed max_tokens=%d", over, max_tokens)
    ctx.log.info(
        "%d chunks, %d tokens max", table.num_rows, max(rows["tokens"], default=0)
    )
    return table
