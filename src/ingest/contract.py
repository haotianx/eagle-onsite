"""Shared types between node scripts and the framework.

A node script is a Python file that defines either

    def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> Records
    def run_corpus(docs: list[Doc], inputs: dict[str, dict[str, Any]], params: dict, ctx: NodeContext) -> dict[str, Records]

and optionally

    def setup(params: dict) -> Any            # once per worker process; result is ctx.state
    def describe(params: dict) -> dict        # version metadata (model id, dim, ...)
    ARTIFACT_TYPE = "parse" | "chunk" | "embed"

For `run`, `inputs` maps each input node name to that node's record for this doc. The root node
(no inputs) receives an empty dict and reads `doc.file`. For `run_corpus`, `inputs` maps each input
node name to {doc_id: record}, and the return value maps doc_id to that doc's record.

A record (`Records`) is a dict (stored as JSON), or a list of dicts or a pyarrow.Table (stored as
Parquet). A checks file defines `validate(record, params) -> list[Issue]`.

Built-in record shapes (for scripts that set ARTIFACT_TYPE):

parse  dict: {"docling": <DoclingDocument.export_to_dict()>,
              "pages": [{"page": int (1-based), "width": float, "height": float,
                         "text_chars": int, "ocr": bool}, ...]}
chunk  table, one row per chunk, columns CHUNK_COLUMNS:
         chunk_id str, doc_id str, seq int, text str, embed_text str, pages list<int>,
         section list<str>, regions list<struct{page int, x0, y0, x1, y1 float}>,
         text_hash str, tokens int
       Region coordinates are PDF points with a top-left origin.
embed  table, columns EMBED_COLUMNS: the chunk columns plus vector fixed_size_list<float32>[dim],
       L2-normalized. describe() returns EMBED_META_KEYS.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

if TYPE_CHECKING:
    import pyarrow as pa

ArtifactType: TypeAlias = Literal["parse", "chunk", "embed"]
IssueLevel: TypeAlias = Literal["error", "warning"]
Records: TypeAlias = "dict[str, Any] | list[dict[str, Any]] | pa.Table"

ARTIFACT_TYPES: tuple[str, ...] = ("parse", "chunk", "embed")

CHUNK_COLUMNS: tuple[str, ...] = (
    "chunk_id",
    "doc_id",
    "seq",
    "text",
    "embed_text",
    "pages",
    "section",
    "regions",
    "text_hash",
    "tokens",
)
EMBED_COLUMNS: tuple[str, ...] = CHUNK_COLUMNS + ("vector",)
EMBED_META_KEYS: tuple[str, ...] = ("model", "revision", "dim", "metric", "query_instruction")
PAGE_KEYS: tuple[str, ...] = ("page", "width", "height", "text_chars", "ocr")


@dataclass(frozen=True)
class Doc:
    """One deduplicated document in a snapshot."""

    id: str
    """SHA-256 hex digest of the file bytes."""
    paths: tuple[str, ...]
    """Source-relative POSIX paths, sorted; paths[0] is the primary path."""
    size: int
    file: Path
    """Absolute path of the primary file."""
    meta: Mapping[str, Any] = field(default_factory=dict)
    """Path-derived metadata: job_number, project, subfolder, filename (values may be None)."""
    flags: tuple[str, ...] = ()
    """Sync flags: "corrupt", "encrypted", "empty"."""


@dataclass(frozen=True)
class Issue:
    level: IssueLevel
    code: str
    message: str
    page: int | None = None


DebugSink: TypeAlias = Callable[[str, Any], None]


class NodeContext:
    """Per-call context passed to run() / run_corpus()."""

    def __init__(
        self,
        *,
        dag: str,
        node: str,
        params: Mapping[str, Any],
        state: Any = None,
        scratch: bool = False,
        logger: logging.Logger | None = None,
        debug_sink: DebugSink | None = None,
    ) -> None:
        self.dag = dag
        self.node = node
        self.params = params
        self.state = state
        """Whatever setup(params) returned, or None."""
        self.scratch = scratch
        self.log = logger or logging.getLogger(f"ingest.node.{node}")
        self._debug_sink = debug_sink

    def debug(self, name: str, data: bytes | str | Mapping[str, Any] | Sequence[Any]) -> None:
        """Save a debug file for the current doc. Kept in scratch runs only; a no-op otherwise."""
        if self.scratch and self._debug_sink is not None:
            self._debug_sink(name, data)


def only(inputs: Mapping[str, Any]) -> Any:
    """Return the record of a node's single input."""
    if len(inputs) != 1:
        raise ValueError(f"expected exactly one input, got {sorted(inputs)}")
    return next(iter(inputs.values()))


_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase and collapse whitespace; the basis of text_hash."""
    return _WS.sub(" ", text).strip().lower()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:32]


def make_chunk_ids(doc_id: str, texts: Sequence[str]) -> list[str]:
    """Stable chunk ids: hash of (doc id, text, occurrence of that text within the doc)."""
    seen: dict[str, int] = {}
    ids = []
    for text in texts:
        n = seen.get(text, 0)
        seen[text] = n + 1
        ids.append(hashlib.sha256(f"{doc_id}\0{text}\0{n}".encode("utf-8")).hexdigest()[:32])
    return ids
