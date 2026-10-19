from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from ingest.contract import Issue, make_chunk_ids, text_hash
from ingest.scripts import LoadedChecks, load_checks
from ingest.store.catalog import Catalog, SnapshotDocInput, VersionDocInput, VersionDocRow
from ingest.store.objects import ObjectStore
from ingest.validation import coverage, hard_checks, quality_checks, revalidate, script_checks


def codes(issues: list[Issue]) -> list[str]:
    return [i.code for i in issues]


def page(n: int, text_chars: int = 100, **kw: Any) -> dict[str, Any]:
    return {"page": n, "width": 612.0, "height": 792.0, "text_chars": text_chars, "ocr": False, **kw}


def parse_record(*pages: dict[str, Any]) -> dict[str, Any]:
    return {"docling": {"name": "doc"}, "pages": list(pages or [page(1), page(2)])}


def chunk_rows(texts: list[str] | None = None, doc_id: str = "d1") -> list[dict[str, Any]]:
    texts = texts or ["Alpha  beta", "Gamma delta", "Epsilon"]
    ids = make_chunk_ids(doc_id, texts)
    return [
        {
            "chunk_id": cid,
            "doc_id": doc_id,
            "seq": i,
            "text": t,
            "embed_text": t,
            "pages": [1],
            "section": ["Intro"],
            "regions": [{"page": 1, "x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}],
            "text_hash": text_hash(t),
            "tokens": 3,
        }
        for i, (cid, t) in enumerate(zip(ids, texts))
    ]


def unit(dim: int, seed: int) -> list[float]:
    v = np.random.default_rng(seed).normal(size=dim)
    return (v / np.linalg.norm(v)).tolist()


def embed_table(rows: list[dict[str, Any]], dim: int = 4, fixed: bool = True) -> pa.Table:
    vectors = [unit(dim, i) for i in range(len(rows))]
    table = pa.Table.from_pylist(rows)
    vtype = pa.list_(pa.float32(), dim) if fixed else pa.list_(pa.float32())
    return table.append_column("vector", pa.array(vectors, type=vtype))


def with_vectors(table: pa.Table, vectors: list[list[float]], vtype: pa.DataType | None = None) -> pa.Table:
    i = table.column_names.index("vector")
    return table.set_column(i, "vector", pa.array(vectors, type=vtype or pa.list_(pa.float32())))


# Untyped records


@pytest.mark.parametrize("record", [{"a": 1}, [{"a": 1}], [], pa.table({"a": [1]})])
def test_untyped_accepts_dict_list_table(record: Any) -> None:
    assert hard_checks(None, record, {}) == []


@pytest.mark.parametrize("record", ["text", 3, [{"a": 1}, 2], None])
def test_untyped_rejects_other(record: Any) -> None:
    issues = hard_checks(None, record, {})
    assert codes(issues) == ["bad_record_type"]
    assert issues[0].level == "error"


# Parse


def test_parse_valid() -> None:
    assert hard_checks("parse", parse_record(), {}) == []


def test_parse_wrong_record_type() -> None:
    assert codes(hard_checks("parse", [{"a": 1}], {})) == ["bad_record_type"]


def test_parse_missing_and_wrong_keys() -> None:
    assert codes(hard_checks("parse", {}, {})) == ["missing_key", "missing_key"]
    issues = hard_checks("parse", {"docling": "x", "pages": {}}, {})
    assert codes(issues) == ["bad_key_type", "bad_key_type"]
    assert "'docling' must be a dict" in issues[0].message


def test_parse_bad_pages_aggregated() -> None:
    pages = [page(1), "nope", {"page": 3}, page(0), page(5, ocr="yes"), page(6, text_chars=True)]
    issues = hard_checks("parse", parse_record(*pages), {})
    by_code = {i.code: [x for x in issues if x.code == i.code] for i in issues}
    assert len(by_code["bad_page_entry"]) == 1
    # page 3 lacks four keys, one issue per key
    assert len(by_code["missing_page_key"]) == 4
    assert all("1 page(s)" in i.message for i in by_code["missing_page_key"])
    bad = {i.message.split("'")[1] for i in by_code["bad_page_value"]}
    assert bad == {"page", "ocr", "text_chars"}


# Chunk


def test_chunk_valid_list_and_table() -> None:
    rows = chunk_rows()
    assert hard_checks("chunk", rows, {}) == []
    assert hard_checks("chunk", pa.Table.from_pylist(rows), {}) == []


def test_chunk_empty_list_and_empty_lists_ok() -> None:
    assert hard_checks("chunk", [], {}) == []
    rows = chunk_rows()
    for r in rows:
        r["section"] = []
        r["regions"] = []
    assert hard_checks("chunk", rows, {}) == []


def test_chunk_rejects_dict() -> None:
    assert codes(hard_checks("chunk", {"a": 1}, {})) == ["bad_record_type"]


def test_chunk_missing_column() -> None:
    rows = [{k: v for k, v in r.items() if k not in ("tokens", "regions")} for r in chunk_rows()]
    issues = hard_checks("chunk", rows, {})
    assert codes(issues) == ["missing_column"]
    assert "regions" in issues[0].message and "tokens" in issues[0].message


def test_chunk_bad_column_types() -> None:
    rows = chunk_rows()
    for r in rows:
        r["seq"] = str(r["seq"])
        r["pages"] = ["1"]
        r["regions"] = [{"page": 1, "x0": 0, "y0": 0, "x1": 1, "y1": 1}]
    issues = hard_checks("chunk", rows, {})
    assert codes(issues) == ["bad_column_type"] * 3
    assert "'seq' has type string, expected integer" in issues[0].message


def test_chunk_nulls() -> None:
    rows = chunk_rows()
    rows[1]["text"] = None
    issues = hard_checks("chunk", rows, {})
    assert codes(issues) == ["null_values"]
    assert "'text'" in issues[0].message


def test_chunk_row_failures_aggregated() -> None:
    rows = chunk_rows(["a", "b", "c", "d", "e"])
    rows[1]["chunk_id"] = rows[0]["chunk_id"]
    for r in rows[2:]:
        r["text"] = "   "
        r["text_hash"] = text_hash("   ")
    rows[0]["text_hash"] = "0" * 32
    issues = hard_checks("chunk", rows, {})
    assert sorted(codes(issues)) == ["bad_text_hash", "duplicate_chunk_id", "empty_text"]
    empty = next(i for i in issues if i.code == "empty_text")
    assert empty.message.startswith("3 row(s)") and rows[2]["chunk_id"] in empty.message


def test_text_hash_normalizes() -> None:
    rows = chunk_rows(["Hello   World"])
    rows[0]["text_hash"] = text_hash("hello world")
    assert hard_checks("chunk", rows, {}) == []


def test_examples_bounded() -> None:
    rows = chunk_rows([f"t{i}" for i in range(20)])
    for r in rows:
        r["text_hash"] = "x"
    (issue,) = hard_checks("chunk", rows, {})
    assert issue.message.startswith("20 row(s)")
    assert issue.message.count("'") == 6 and "…" in issue.message


# Embed


def test_embed_valid() -> None:
    rows = chunk_rows()
    table = embed_table(rows)
    assert hard_checks("embed", table, {}, inputs={"chunk": rows}, meta={"dim": 4}) == []
    assert hard_checks("embed", embed_table(rows, fixed=False), {}, meta={"dim": 4}) == []
    assert hard_checks("embed", table.to_pylist(), {}, meta={"dim": 4}) == []


def test_embed_missing_vector_and_chunk_columns() -> None:
    issues = hard_checks("embed", chunk_rows(), {})
    assert codes(issues) == ["missing_column"]
    assert "vector" in issues[0].message


def test_embed_bad_vector_type() -> None:
    table = pa.Table.from_pylist(chunk_rows()).append_column("vector", pa.array([[1], [2], [3]]))
    issues = hard_checks("embed", table, {})
    assert codes(issues) == ["bad_column_type"]
    assert "'vector'" in issues[0].message


def test_embed_bad_dim() -> None:
    table = embed_table(chunk_rows(), dim=3)
    issues = hard_checks("embed", table, {}, meta={"dim": 4})
    assert codes(issues) == ["bad_vector_dim"]
    assert "length 3, expected 4" in issues[0].message


def test_embed_ragged_without_dim() -> None:
    table = with_vectors(embed_table(chunk_rows(), fixed=False), [unit(4, 0), unit(3, 1), unit(4, 2)])
    assert codes(hard_checks("embed", table, {})) == ["ragged_vectors"]
    assert codes(hard_checks("embed", table, {}, meta={"dim": 4})) == ["bad_vector_dim"]


def test_embed_nonfinite_and_unnormalized() -> None:
    table = with_vectors(
        embed_table(chunk_rows()),
        [[float("nan"), 0.0, 0.0, 1.0], [1.0, 1.0, 0.0, 0.0], [0.0, float("inf"), 0.0, 0.0]],
        pa.list_(pa.float32(), 4),
    )
    issues = hard_checks("embed", table, {}, meta={"dim": 4})
    assert codes(issues) == ["nonfinite_vector", "vector_not_normalized"]
    assert issues[0].message.startswith("2 row(s)")
    assert issues[1].message.startswith("1 row(s)")


def test_embed_norm_tolerance() -> None:
    v = np.array([1.0 + 5e-4, 0.0, 0.0, 0.0])
    table = with_vectors(embed_table(chunk_rows(["a"])), [v.tolist()], pa.list_(pa.float64(), 4))
    assert hard_checks("embed", table, {}) == []


def test_embed_one_vector_per_chunk() -> None:
    rows = chunk_rows()
    table = embed_table(rows)
    missing = table.slice(0, 2)
    (issue,) = hard_checks("embed", missing, {}, inputs={"chunk": rows})
    assert issue.code == "vector_chunk_mismatch" and "without a vector" in issue.message
    extra = embed_table(rows + chunk_rows(["zeta"]))
    (issue,) = hard_checks("embed", extra, {}, inputs={"chunk": pa.Table.from_pylist(rows)})
    assert issue.code == "vector_chunk_mismatch" and "not in the input" in issue.message
    (issue,) = hard_checks("embed", [], {}, inputs={"chunk": rows})
    assert issue.code == "vector_chunk_mismatch"
    # several inputs or a non-table input: not checked
    assert hard_checks("embed", missing, {}, inputs={"a": rows, "b": rows}) == []
    assert hard_checks("embed", missing, {}, inputs={"parse": parse_record()}) == []


def test_embed_duplicate_vector_for_chunk() -> None:
    rows = chunk_rows()
    table = embed_table(rows + rows[:1])
    issues = hard_checks("embed", table, {}, inputs={"chunk": rows})
    assert codes(issues) == ["duplicate_chunk_id", "vector_chunk_mismatch"]
    assert "different count" in issues[1].message


# Quality


def test_quality_pages_without_text() -> None:
    issues = quality_checks("parse", parse_record(page(1), page(2, 0), page(3, 0)), {})
    assert [(i.level, i.code, i.page) for i in issues] == [("warning", "page_no_text", 2), ("warning", "page_no_text", 3)]
    assert quality_checks("parse", {"bad": 1}, {}) == []


def test_quality_max_tokens() -> None:
    rows = chunk_rows()
    rows[0]["tokens"] = 900
    assert quality_checks("chunk", rows, {}) == []
    (issue,) = quality_checks("chunk", rows, {"max_tokens": 512})
    assert (issue.level, issue.code) == ("warning", "chunk_over_max_tokens")
    assert rows[0]["chunk_id"] in issue.message and "512" in issue.message


def test_quality_zero_rows() -> None:
    assert codes(quality_checks("chunk", [], {})) == ["no_chunks"]
    empty = embed_table(chunk_rows()).slice(0, 0)
    assert codes(quality_checks("embed", empty, {})) == ["no_chunks"]
    assert quality_checks("chunk", chunk_rows(), {}) == []
    assert quality_checks(None, [], {}) == []


# Script checks

CHECKS = """
from ingest.contract import Issue

def validate(record, params):
    mode = params.get("mode", "warn")
    if mode == "raise":
        raise RuntimeError("boom")
    if mode == "tuple":
        return ()
    if mode == "junk":
        return [Issue("warning", "ok", "fine"), {"code": "x"}]
    if isinstance(record, dict):
        return [Issue("warning", "json_doc", "a json doc", page=1)]
    return [Issue("warning", "rows", f"{record.num_rows} rows")]
"""


@pytest.fixture
def checks(tmp_path: Path) -> LoadedChecks:
    path = tmp_path / "checks" / "checks.py"
    path.parent.mkdir()
    path.write_text(CHECKS)
    return load_checks(path)


def test_script_checks_returns_issues(checks: LoadedChecks) -> None:
    assert script_checks(checks, {"a": 1}, {}) == [Issue("warning", "json_doc", "a json doc", page=1)]


def test_script_checks_crash(checks: LoadedChecks) -> None:
    (issue,) = script_checks(checks, {}, {"mode": "raise"})
    assert (issue.level, issue.code) == ("error", "check_crashed")
    assert "RuntimeError: boom" in issue.message


@pytest.mark.parametrize("mode", ["tuple", "junk"])
def test_script_checks_bad_result(checks: LoadedChecks, mode: str) -> None:
    (issue,) = script_checks(checks, {}, {"mode": mode})
    assert (issue.level, issue.code) == ("error", "bad_check_result")


# Coverage


def vdoc(doc_id: str, status: str = "ok") -> VersionDocRow:
    return VersionDocRow(doc_id=doc_id, status=status, version_id=1)  # type: ignore[arg-type]


def test_coverage_complete() -> None:
    assert coverage(["a", "b", "c"], [vdoc("a"), vdoc("b", "error"), vdoc("c", "skipped")]) == []


def test_coverage_missing_and_extra() -> None:
    result = coverage(["a", "b", "c"], [vdoc("a"), vdoc("z")])
    assert [(d, i.level, i.code) for d, i in result] == [
        ("b", "error", "doc_missing"),
        ("c", "error", "doc_missing"),
        ("z", "error", "doc_not_in_snapshot"),
    ]


# Revalidate


@pytest.fixture
def cat(tmp_path: Path) -> Iterator[Catalog]:
    c = Catalog(tmp_path / "catalog.sqlite")
    yield c
    c.close()


def test_revalidate(cat: Catalog, tmp_path: Path, checks: LoadedChecks) -> None:
    objects = ObjectStore(tmp_path / "objects")
    snap = cat.create_snapshot(
        "src", [SnapshotDocInput(doc_id=d, paths=(f"{d}.pdf",), size=1, mtime_ns=1) for d in ("d1", "d2", "d3")], 3
    )
    version = cat.create_version(
        dag="default", node="chunk", node_fp="fp", snapshot_id=snap, input_versions={}, run_id=None,
        commit=None, dirty=False, scratch=False, artifact_type="chunk", meta={},
    )
    sha1, kind1 = objects.put(chunk_rows())
    sha2, kind2 = objects.put({"x": 1})
    cat.add_version_docs(
        version.id,
        [
            VersionDocInput(doc_id="d1", status="ok", object_sha=sha1, kind=kind1),
            VersionDocInput(doc_id="d2", status="ok", object_sha=sha2, kind=kind2),
            VersionDocInput(doc_id="d3", status="error", error_message="failed"),
        ],
    )
    cat.add_issues(version.id, "hard", [("d3", Issue("error", "missing_column", "m"))])
    cat.add_issues(version.id, "quality", [("d1", Issue("warning", "no_chunks", "q"))])
    cat.add_issues(version.id, "script", [("d1", Issue("error", "stale", "old"))], checks_fp="old")
    docs_before = cat.version_docs(version.id)

    assert revalidate(cat, objects, version.id, checks, {}, "fp1") == 2
    rows = cat.issues(version.id)
    script = sorted((r.doc_id, r.code, r.message, r.checks_fp, r.page) for r in rows if r.source == "script")
    assert script == [("d1", "rows", "3 rows", "fp1", None), ("d2", "json_doc", "a json doc", "fp1", 1)]
    assert sorted((r.source, r.code) for r in rows if r.source != "script") == [
        ("hard", "missing_column"),
        ("quality", "no_chunks"),
    ]
    assert cat.version_docs(version.id) == docs_before

    assert revalidate(cat, objects, version.id, checks, {"mode": "raise"}, "fp2") == 2
    assert {r.code for r in cat.issues(version.id) if r.source == "script"} == {"check_crashed"}

    assert cat.get_version(version.id).meta["checks_fp"] == "fp2"

    assert revalidate(cat, objects, version.id, None, {}, None) == 0
    assert sorted(r.source for r in cat.issues(version.id)) == ["hard", "quality"]
    assert cat.version_docs(version.id) == docs_before
    assert cat.get_version(version.id).meta["checks_fp"] is None
