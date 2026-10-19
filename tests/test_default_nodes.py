"""The default DAG's node scripts, chained ocr -> chunk -> embed over the fixtures."""

from __future__ import annotations

import hashlib
import importlib.util
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
import yaml

from ingest import contract
from ingest.contract import Doc, NodeContext

pytestmark = pytest.mark.models

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "proposals"
SZEKERES = "2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf"
ADDENDUM = "3426 AGUILAR COLINA FUERTE/CONTRACT/PROPOSAL ADDENDUM.pdf"


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"nodes_{name}", REPO / "nodes" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node_params(node: str) -> dict[str, Any]:
    dag = yaml.safe_load((REPO / "dags" / "default.yaml").read_text())
    return dag["nodes"][node]["params"]


class Node:
    def __init__(self, node: str, script: str) -> None:
        self.name = node
        self.module = load_script(script)
        self.params = node_params(node)
        self.ctx = NodeContext(
            dag="default", node=node, params=self.params, state=self.module.setup(self.params)
        )

    def run(self, doc: Doc, inputs: dict[str, Any]) -> Any:
        return self.module.run(doc, inputs, self.params, self.ctx)


def make_docs(root: Path) -> dict[str, Doc]:
    """Fixture docs keyed by primary path, deduplicated by content hash like a snapshot."""
    by_hash: dict[str, list[Path]] = {}
    for path in sorted(root.rglob("*.pdf")):
        by_hash.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), []).append(path)
    docs = {}
    for digest, files in by_hash.items():
        paths = tuple(sorted(f.relative_to(root).as_posix() for f in files))
        primary = root / paths[0]
        docs[paths[0]] = Doc(
            id=digest,
            paths=paths,
            size=primary.stat().st_size,
            file=primary,
            meta={"filename": primary.name},
        )
    return docs


@pytest.fixture(scope="module")
def docs() -> dict[str, Doc]:
    return make_docs(FIXTURES)


@pytest.fixture(scope="module")
def parsed(docs: dict[str, Doc]) -> dict[str, dict[str, Any]]:
    node = Node("ocr", "docling_parse")
    out = {}
    start = time.perf_counter()
    for path, doc in docs.items():
        out[path] = node.run(doc, {})
    pages = sum(len(r["pages"]) for r in out.values())
    print(f"\nparse: {pages} pages in {time.perf_counter() - start:.1f}s")
    return out


@pytest.fixture(scope="module")
def chunked(docs: dict[str, Doc], parsed: dict[str, dict[str, Any]]) -> dict[str, pa.Table]:
    node = Node("chunk", "hybrid_chunk")
    return {path: node.run(docs[path], {"ocr": parsed[path]}) for path in docs}


@pytest.fixture(scope="module")
def embed_node() -> Node:
    return Node("embed", "embed")


@pytest.fixture(scope="module")
def embedded(
    docs: dict[str, Doc], chunked: dict[str, pa.Table], embed_node: Node
) -> dict[str, pa.Table]:
    return {path: embed_node.run(docs[path], {"chunk": chunked[path]}) for path in docs}


def doc_text(record: dict[str, Any]) -> str:
    from docling_core.types.doc import DoclingDocument

    return DoclingDocument.model_validate(record["docling"]).export_to_text()


def test_fixture_docs(docs: dict[str, Doc]) -> None:
    assert len(docs) == 6


def test_parse_record_shape(parsed: dict[str, dict[str, Any]]) -> None:
    for record in parsed.values():
        assert set(record) == {"docling", "pages"}
        assert [p["page"] for p in record["pages"]] == list(range(1, len(record["pages"]) + 1))
        for page in record["pages"]:
            assert tuple(page) == contract.PAGE_KEYS
            assert page["width"] > 0 and page["height"] > 0


def test_parse_born_digital(parsed: dict[str, dict[str, Any]]) -> None:
    record = parsed[SZEKERES]
    text = doc_text(record)
    assert "1,750.00" in text
    assert "1,835.00" in text
    assert len(record["pages"]) == 2
    assert not any(p["ocr"] for p in record["pages"])


def test_parse_scan(parsed: dict[str, dict[str, Any]]) -> None:
    record = parsed[ADDENDUM]
    assert record["pages"]
    for page in record["pages"]:
        assert page["ocr"]
        assert page["text_chars"] > 0
    assert "Title Report" in doc_text(record)


def test_parse_describe() -> None:
    meta = load_script("docling_parse").describe(node_params("ocr"))
    assert meta["engine"] == "docling"
    assert meta["ocr_engine"] == "ocrmac"


def test_chunk_tables(
    chunked: dict[str, pa.Table], parsed: dict[str, dict[str, Any]], docs: dict[str, Doc]
) -> None:
    schema = load_script("hybrid_chunk").SCHEMA
    max_tokens = node_params("chunk")["max_tokens"]
    all_ids: list[str] = []
    for path, table in chunked.items():
        assert table.schema == schema
        assert tuple(table.column_names) == contract.CHUNK_COLUMNS
        assert table.num_rows > 0, path
        rows = table.to_pylist()
        heights = {p["page"]: p["height"] for p in parsed[path]["pages"]}
        widths = {p["page"]: p["width"] for p in parsed[path]["pages"]}
        assert [r["seq"] for r in rows] == list(range(len(rows)))
        for row in rows:
            assert row["doc_id"] == docs[path].id
            assert row["text_hash"] == contract.text_hash(row["text"])
            assert row["tokens"] <= max_tokens
            assert row["embed_text"].startswith(f"Document: {Path(path).name}\nPath: {path}\n")
            assert row["regions"] and row["pages"]
            for region in row["regions"]:
                assert 0 <= region["x0"] <= region["x1"] <= widths[region["page"]] + 1
                assert 0 <= region["y0"] <= region["y1"] <= heights[region["page"]] + 1
        all_ids += [r["chunk_id"] for r in rows]
    assert len(all_ids) == len(set(all_ids))


def test_chunk_empty(docs: dict[str, Doc]) -> None:
    from docling_core.types.doc import DoclingDocument

    node = Node("chunk", "hybrid_chunk")
    record = {"docling": DoclingDocument(name="empty").export_to_dict(), "pages": []}
    table = node.run(docs[SZEKERES], {"ocr": record})
    assert table.num_rows == 0
    assert table.schema == node.module.SCHEMA


def test_embed_vectors(embedded: dict[str, pa.Table], chunked: dict[str, pa.Table]) -> None:
    for path, table in embedded.items():
        assert tuple(table.column_names) == contract.EMBED_COLUMNS
        assert table.schema.field("vector").type == pa.list_(pa.float32(), 1024)
        assert table.drop_columns(["vector"]).equals(chunked[path])
        vectors = np.stack(table.column("vector").to_numpy(zero_copy_only=False))
        assert not np.isnan(vectors).any()
        np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3)


def test_embed_empty(docs: dict[str, Doc], chunked: dict[str, pa.Table], embed_node: Node) -> None:
    empty = chunked[SZEKERES].slice(0, 0)
    table = embed_node.run(docs[SZEKERES], {"chunk": empty})
    assert table.num_rows == 0
    assert table.schema.field("vector").type == pa.list_(pa.float32(), 1024)


def test_embed_describe() -> None:
    meta = load_script("embed").describe(node_params("embed"))
    assert tuple(meta) == contract.EMBED_META_KEYS
    assert meta["dim"] == 1024
    assert meta["metric"] == "cosine"
    assert meta["revision"] == node_params("embed")["revision"]


def test_embed_query_ranks_szekeres_first(
    embedded: dict[str, pa.Table], embed_node: Node
) -> None:
    meta = embed_node.module.describe(embed_node.params)
    table = pa.concat_tables(embedded.values())
    vectors = np.stack(table.column("vector").to_numpy(zero_copy_only=False))
    query = embed_node.ctx.state.encode(
        [meta["query_instruction"] + "aerial mapping fee"], normalize_embeddings=True
    )[0]
    best = int(np.argmax(vectors @ query))
    assert table.column("doc_id")[best].as_py() == make_docs(FIXTURES)[SZEKERES].id
