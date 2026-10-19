"""Built-in hard and quality checks, script-declared checks, coverage, revalidation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from ingest.contract import CHUNK_COLUMNS, PAGE_KEYS, ArtifactType, Issue, IssueLevel, text_hash
from ingest.scripts import LoadedChecks
from ingest.store.catalog import Catalog, VersionDocRow
from ingest.store.objects import ObjectStore

NORM_TOL = 1e-3
MAX_EXAMPLES = 3

_REGION_COORDS = ("x0", "y0", "x1", "y1")


def _error(code: str, message: str) -> Issue:
    return Issue("error", code, message)


def _examples(items: Sequence[Any]) -> str:
    shown = ", ".join(repr(i) for i in items[:MAX_EXAMPLES])
    return shown + (", …" if len(items) > MAX_EXAMPLES else "")


def _rows_issue(code: str, what: str, ids: Sequence[Any], level: IssueLevel = "error") -> Issue:
    """One issue for a per-row failure: count plus a few example ids."""
    return Issue(level, code, f"{len(ids)} row(s) {what} (e.g. {_examples(ids)})")


def _as_table(record: Any) -> pa.Table | None:
    if isinstance(record, pa.Table):
        return record
    if isinstance(record, list) and all(isinstance(r, dict) for r in record):
        return pa.Table.from_pylist(record)
    return None


def _kind(record: Any) -> str:
    if isinstance(record, list):
        bad = sorted({type(r).__name__ for r in record if not isinstance(r, dict)})
        return f"list containing {', '.join(bad)}"
    return type(record).__name__


# Arrow type predicates

def _is_str(t: pa.DataType) -> bool:
    return pa.types.is_string(t) or pa.types.is_large_string(t)


def _is_list(t: pa.DataType) -> bool:
    return pa.types.is_list(t) or pa.types.is_large_list(t) or pa.types.is_fixed_size_list(t)


def _list_of(pred: Callable[[pa.DataType], bool]) -> Callable[[pa.DataType], bool]:
    # A list whose values are all empty infers as list<null>; that is still a valid list.
    return lambda t: _is_list(t) and (pred(t.value_type) or pa.types.is_null(t.value_type))


def _is_region(t: pa.DataType) -> bool:
    if not pa.types.is_struct(t):
        return False
    fields = {t.field(i).name: t.field(i).type for i in range(t.num_fields)}
    return (
        "page" in fields
        and pa.types.is_integer(fields["page"])
        and all(c in fields and pa.types.is_floating(fields[c]) for c in _REGION_COORDS)
    )


_COLUMN_TYPES: dict[str, tuple[Callable[[pa.DataType], bool], str]] = {
    "chunk_id": (_is_str, "string"),
    "doc_id": (_is_str, "string"),
    "seq": (pa.types.is_integer, "integer"),
    "text": (_is_str, "string"),
    "embed_text": (_is_str, "string"),
    "pages": (_list_of(pa.types.is_integer), "list<integer>"),
    "section": (_list_of(_is_str), "list<string>"),
    "regions": (
        _list_of(_is_region),
        "list<struct{page: integer, x0, y0, x1, y1: floating}>",
    ),
    "text_hash": (_is_str, "string"),
    "tokens": (pa.types.is_integer, "integer"),
    "vector": (_list_of(pa.types.is_floating), "fixed_size_list or list of floating"),
}


# Hard checks

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
    if artifact_type is None:
        if isinstance(record, dict) or _as_table(record) is not None:
            return []
        return [_error("bad_record_type", f"record must be a dict, list of dicts or pyarrow.Table, got {_kind(record)}")]
    if artifact_type == "parse":
        return _parse_hard(record)
    table = _as_table(record)
    if table is None:
        return [_error("bad_record_type", f"{artifact_type} record must be a table or list of dicts, got {_kind(record)}")]
    if artifact_type == "chunk":
        return _chunk_hard(table, CHUNK_COLUMNS)
    if artifact_type == "embed":
        return _embed_hard(table, inputs or {}, meta or {})
    raise ValueError(f"unknown artifact type {artifact_type!r}")


def _parse_hard(record: Any) -> list[Issue]:
    if not isinstance(record, dict):
        return [_error("bad_record_type", f"parse record must be a dict, got {_kind(record)}")]
    issues = []
    for key, typ, name in (("docling", dict, "dict"), ("pages", list, "list")):
        if key not in record:
            issues.append(_error("missing_key", f"parse record has no {key!r} key"))
        elif not isinstance(record[key], typ):
            issues.append(_error("bad_key_type", f"{key!r} must be a {name}, got {type(record[key]).__name__}"))
    pages = record.get("pages")
    if not isinstance(pages, list):
        return issues
    not_dict, missing, bad = [], Counter[str](), Counter[str]()
    examples: dict[str, list[int]] = {}
    for i, p in enumerate(pages):
        if not isinstance(p, dict):
            not_dict.append(i)
            continue
        for key in PAGE_KEYS:
            if key not in p:
                missing[key] += 1
                examples.setdefault(f"missing:{key}", []).append(i)
            elif not _page_value_ok(key, p[key]):
                bad[key] += 1
                examples.setdefault(f"bad:{key}", []).append(i)
    if not_dict:
        issues.append(_error("bad_page_entry", f"{len(not_dict)} pages entr(ies) are not dicts (e.g. index {_examples(not_dict)})"))
    for key, n in missing.items():
        ex = _examples(examples[f"missing:{key}"])
        issues.append(_error("missing_page_key", f"{n} page(s) missing {key!r} (e.g. index {ex})"))
    for key, n in bad.items():
        ex = _examples(examples[f"bad:{key}"])
        issues.append(_error("bad_page_value", f"{n} page(s) have an invalid {key!r}, expected {_PAGE_EXPECT[key]} (e.g. index {ex})"))
    return issues


_PAGE_EXPECT = {
    "page": "int >= 1",
    "width": "number > 0",
    "height": "number > 0",
    "text_chars": "int >= 0",
    "ocr": "bool",
}


def _page_value_ok(key: str, v: Any) -> bool:
    is_int = isinstance(v, int) and not isinstance(v, bool)
    is_num = (is_int or isinstance(v, float)) and np.isfinite(v)
    if key == "page":
        return is_int and v >= 1
    if key in ("width", "height"):
        return bool(is_num) and v > 0
    if key == "text_chars":
        return is_int and v >= 0
    return isinstance(v, bool)


def _columns_issues(table: pa.Table, columns: Sequence[str]) -> tuple[list[Issue], set[str]]:
    """Missing columns, wrong types and nulls; returns the issues and the columns that are usable.
    An empty list record has no schema, so it has no column issues."""
    issues: list[Issue] = []
    ok: set[str] = set()
    if table.num_rows == 0 and table.num_columns == 0:
        return issues, ok
    missing = [c for c in columns if c not in table.column_names]
    if missing:
        issues.append(_error("missing_column", f"missing column(s): {', '.join(missing)}"))
    for c in columns:
        if c in missing:
            continue
        typ = table.schema.field(c).type
        pred, expected = _COLUMN_TYPES[c]
        if not pred(typ):
            issues.append(_error("bad_column_type", f"column {c!r} has type {typ}, expected {expected}"))
            continue
        nulls = table.column(c).null_count
        if nulls:
            issues.append(_error("null_values", f"column {c!r} has {nulls} null value(s)"))
            continue
        ok.add(c)
    return issues, ok


def _row_ids(table: pa.Table, ok: set[str]) -> list[Any]:
    if "chunk_id" in ok:
        return table.column("chunk_id").to_pylist()
    return [f"row {i}" for i in range(table.num_rows)]


def _chunk_hard(table: pa.Table, columns: Sequence[str]) -> list[Issue]:
    issues, ok = _columns_issues(table, columns)
    ids = _row_ids(table, ok)
    if "chunk_id" in ok:
        dupes = [cid for cid, n in Counter(ids).items() if n > 1]
        if dupes:
            issues.append(_error("duplicate_chunk_id", f"{len(dupes)} chunk_id(s) appear more than once (e.g. {_examples(dupes)})"))
    if "text" in ok:
        texts = table.column("text").to_pylist()
        empty = [i for i, t in zip(ids, texts) if not t.strip()]
        if empty:
            issues.append(_rows_issue("empty_text", "have empty text", empty))
        if "text_hash" in ok:
            hashes = table.column("text_hash").to_pylist()
            wrong = [i for i, t, h in zip(ids, texts, hashes) if h != text_hash(t)]
            if wrong:
                issues.append(_rows_issue("bad_text_hash", "have a text_hash that does not match text_hash(text)", wrong))
    return issues


def _embed_hard(table: pa.Table, inputs: Mapping[str, Any], meta: Mapping[str, Any]) -> list[Issue]:
    issues = _chunk_hard(table, CHUNK_COLUMNS + ("vector",))
    ok = _columns_issues(table, ("chunk_id", "vector"))[1]
    ids = _row_ids(table, ok)
    if "vector" in ok and table.num_rows:
        issues += _vector_issues(table.column("vector"), ids, meta.get("dim"))
    if table.num_rows == 0 and table.num_columns == 0:
        ok.add("chunk_id")
    if "chunk_id" in ok and len(inputs) == 1:
        source = _as_table(next(iter(inputs.values())))
        if source is not None and "chunk_id" in source.column_names and _is_str(source.schema.field("chunk_id").type):
            issue = _vector_per_chunk(ids, source.column("chunk_id").to_pylist())
            if issue:
                issues.append(issue)
    return issues


def _vector_issues(column: pa.ChunkedArray, ids: list[Any], dim: Any) -> list[Issue]:
    arr = column.combine_chunks()
    if pa.types.is_fixed_size_list(arr.type):
        lengths = np.full(len(arr), arr.type.list_size)
    else:
        lengths = pc.list_value_length(arr).to_numpy(zero_copy_only=False)
    issues = []
    if dim is not None:
        wrong = [i for i, n in zip(ids, lengths) if n != dim]
        if wrong:
            got = sorted({int(n) for n in lengths if n != dim})
            issues.append(_rows_issue("bad_vector_dim", f"have vector length {_examples(got)}, expected {dim}", wrong))
            return issues
    elif len(set(lengths.tolist())) > 1:
        got = sorted({int(n) for n in lengths})
        issues.append(_error("ragged_vectors", f"vectors have differing lengths {_examples(got)}"))
        return issues
    n = int(lengths[0])
    if n == 0:
        issues.append(_error("bad_vector_dim", "vectors are empty"))
        return issues
    values = pc.list_flatten(arr).to_numpy(zero_copy_only=False).astype(np.float64).reshape(len(arr), n)
    finite = np.isfinite(values).all(axis=1)
    nonfinite = [i for i, f in zip(ids, finite) if not f]
    if nonfinite:
        issues.append(_rows_issue("nonfinite_vector", "have NaN, inf or null vector values", nonfinite))
    norms = np.linalg.norm(np.where(np.isfinite(values), values, 0.0), axis=1)
    unnormed = [i for i, f, nrm in zip(ids, finite, norms) if f and abs(nrm - 1.0) > NORM_TOL]
    if unnormed:
        issues.append(_rows_issue("vector_not_normalized", f"have L2 norm outside 1 ± {NORM_TOL}", unnormed))
    return issues


def _vector_per_chunk(ids: list[Any], expected: list[Any]) -> Issue | None:
    have, want = Counter(ids), Counter(expected)
    if have == want and all(n == 1 for n in have.values()):
        return None
    missing = sorted(set(want) - set(have))
    extra = sorted(set(have) - set(want))
    parts = [f"{len(ids)} vector row(s) for {len(expected)} input chunk(s)"]
    if missing:
        parts.append(f"{len(missing)} input chunk_id(s) without a vector (e.g. {_examples(missing)})")
    if extra:
        parts.append(f"{len(extra)} chunk_id(s) not in the input (e.g. {_examples(extra)})")
    if not missing and not extra:
        dupes = sorted(c for c in have if have[c] != want[c])
        parts.append(f"{len(dupes)} chunk_id(s) with a different count (e.g. {_examples(dupes)})")
    return _error("vector_chunk_mismatch", "; ".join(parts))


# Quality checks

def quality_checks(artifact_type: ArtifactType | None, record: Any, params: Mapping[str, Any]) -> list[Issue]:
    """Warning-level: parse pages with text_chars == 0 (one issue per page, page set);
    chunk rows with tokens > params["max_tokens"] when that param exists; chunk/embed tables
    with zero rows."""
    if artifact_type == "parse":
        pages = record.get("pages") if isinstance(record, dict) else None
        if not isinstance(pages, list):
            return []
        return [
            Issue("warning", "page_no_text", f"page {p['page']} has no text", page=p["page"])
            for p in pages
            if isinstance(p, dict)
            and p.get("text_chars") == 0
            and isinstance(p.get("page"), int)
            and not isinstance(p.get("page"), bool)
        ]
    if artifact_type not in ("chunk", "embed"):
        return []
    table = _as_table(record)
    if table is None:
        return []
    if table.num_rows == 0:
        return [Issue("warning", "no_chunks", f"{artifact_type} table has zero rows")]
    issues = []
    max_tokens = params.get("max_tokens")
    if (
        artifact_type == "chunk"
        and max_tokens is not None
        and "tokens" in table.column_names
        and pa.types.is_integer(table.schema.field("tokens").type)
    ):
        ok = {"chunk_id"} if "chunk_id" in table.column_names and _is_str(table.schema.field("chunk_id").type) else set()
        ids = _row_ids(table, ok)
        tokens = table.column("tokens").to_pylist()
        over = [i for i, t in zip(ids, tokens) if t is not None and t > max_tokens]
        if over:
            issues.append(_rows_issue("chunk_over_max_tokens", f"have tokens > max_tokens ({max_tokens})", over, "warning"))
    return issues


# Script checks

def script_checks(checks: LoadedChecks, record: Any, params: Mapping[str, Any]) -> list[Issue]:
    """Call checks.validate; an exception becomes one error Issue (code "check_crashed") and a
    non-list or non-Issue result one error Issue (code "bad_check_result")."""
    try:
        result = checks.validate(record, params)
    except Exception as e:
        return [_error("check_crashed", f"{checks.path.name}: validate() raised {type(e).__name__}: {e}")]
    if not isinstance(result, list):
        return [_error("bad_check_result", f"{checks.path.name}: validate() returned {type(result).__name__}, expected list[Issue]")]
    bad = sorted({type(i).__name__ for i in result if not isinstance(i, Issue)})
    if bad:
        return [_error("bad_check_result", f"{checks.path.name}: validate() returned non-Issue items ({', '.join(bad)})")]
    return result


# Coverage

def coverage(snapshot_doc_ids: Sequence[str], docs: Sequence[VersionDocRow]) -> list[tuple[str, Issue]]:
    """Error issues for snapshot docs missing from the version, and for version docs not in the
    snapshot."""
    expected = set(snapshot_doc_ids)
    present = {d.doc_id for d in docs}
    issues = [
        (doc_id, _error("doc_missing", "snapshot doc has no entry in the version"))
        for doc_id in sorted(expected - present)
    ]
    issues += [
        (doc_id, _error("doc_not_in_snapshot", "version doc is not in the version's snapshot"))
        for doc_id in sorted(present - expected)
    ]
    return issues


# Revalidate

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
    issues: list[tuple[str | None, Issue]] = []
    if checks is not None:
        for doc in catalog.version_docs(version_id):
            if doc.status != "ok" or doc.object_sha is None or doc.kind is None:
                continue
            record = objects.get(doc.object_sha, doc.kind)  # type: ignore[arg-type]
            issues += [(doc.doc_id, i) for i in script_checks(checks, record, params)]
    catalog.replace_issues(version_id, "script", issues, checks_fp=checks_fp)
    return len(issues)
