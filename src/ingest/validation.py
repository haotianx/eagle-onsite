"""Built-in hard and quality checks, script-declared checks, coverage, revalidation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ingest.contract import ArtifactType, Issue
from ingest.scripts import LoadedChecks
from ingest.store.catalog import Catalog, VersionDocRow
from ingest.store.objects import ObjectStore


def hard_checks(
    artifact_type: ArtifactType | None,
    record: Any,
    params: Mapping[str, Any],
    *,
    inputs: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> list[Issue]:
    """Error-level issues for built-in record shapes (contract.py docstring). None type → only
    that the record is a dict, list of dicts or pyarrow.Table.
      parse: keys "docling" (dict) and "pages" (list of dicts with contract.PAGE_KEYS).
      chunk: CHUNK_COLUMNS present with the right types; chunk_id unique; text non-empty;
             text_hash matches contract.text_hash(text).
      embed: EMBED_COLUMNS present; vector length == meta["dim"] when given, no NaN/inf,
             unit L2 norm (±1e-3); when the single input is a chunk table, one row per input
             chunk_id.
    Codes are short snake_case, e.g. "missing_column", "bad_vector_dim"."""
    raise NotImplementedError


def quality_checks(artifact_type: ArtifactType | None, record: Any, params: Mapping[str, Any]) -> list[Issue]:
    """Warning-level: parse pages with text_chars == 0 (one issue per page, page set);
    chunk rows with tokens > params["max_tokens"] when that param exists; chunk/embed tables
    with zero rows."""
    raise NotImplementedError


def script_checks(checks: LoadedChecks, record: Any, params: Mapping[str, Any]) -> list[Issue]:
    """Call checks.validate; an exception becomes one error Issue (code "check_crashed") and a
    non-list or non-Issue result one error Issue (code "bad_check_result")."""
    raise NotImplementedError


def coverage(snapshot_doc_ids: Sequence[str], docs: Sequence[VersionDocRow]) -> list[tuple[str, Issue]]:
    """Error issues for snapshot docs missing from the version, and for version docs not in the
    snapshot."""
    raise NotImplementedError


def revalidate(
    catalog: Catalog,
    objects: ObjectStore,
    version_id: int,
    checks: LoadedChecks | None,
    params: Mapping[str, Any],
    checks_fp: str | None,
) -> int:
    """Re-run script checks over the version's ok docs and replace its 'script' issues (records
    and doc statuses unchanged). checks None clears them. Returns the number of issues stored."""
    raise NotImplementedError
