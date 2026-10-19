"""Per-doc metrics by artifact type, version summaries, and version comparison (Compare)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ingest.contract import ArtifactType
from ingest.store.catalog import Catalog
from ingest.store.objects import ObjectStore


def doc_metrics(artifact_type: ArtifactType | None, record: Any) -> dict[str, float]:
    """Always "records" (1 for a dict, row count for tables). parse adds pages, ocr_pages,
    tables (count of docling tables); chunk adds chunks, tokens_mean; embed adds vectors."""
    raise NotImplementedError


def version_summary(catalog: Catalog, objects: ObjectStore, version_id: int) -> dict[str, Any]:
    """Generic: docs, ok, failed, skipped, records, size_bytes; plus type-specific totals:
    parse pages, ocr_pages, tables; chunk chunks, tokens_per_chunk; embed vectors, model."""
    raise NotImplementedError


@dataclass(frozen=True)
class DocChange:
    doc_id: str
    a_records: int | None
    """None when the doc isn't ok in version a."""
    b_records: int | None
    change: float
    """Size of change used for sorting: for tables, rows whose text_hash (or full row when there
    is no text_hash) appears in only one side; for JSON, 0 if identical else max(records)."""


@dataclass(frozen=True)
class Comparison:
    a: Mapping[str, Any]
    b: Mapping[str, Any]
    """version_summary of each side."""
    changed: tuple[DocChange, ...]
    """Docs with change > 0 or present on one side only, sorted by change descending."""
    unchanged: int


def compare_versions(catalog: Catalog, objects: ObjectStore, a_id: int, b_id: int) -> Comparison:
    raise NotImplementedError


def doc_diff(catalog: Catalog, objects: ObjectStore, a_id: int, b_id: int, doc_id: str) -> dict[str, Any]:
    """{"a": record or None, "b": record or None} (tables as lists of dicts) plus for tables
    "only_a" / "only_b": text_hash values present on one side."""
    raise NotImplementedError
