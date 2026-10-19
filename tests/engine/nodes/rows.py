"""Table node: n rows per doc derived from the size record."""

from __future__ import annotations

from typing import Any

from ingest.contract import Doc, NodeContext, only


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> list[dict[str, Any]]:
    size = only(inputs)["size"]
    return [{"doc_id": doc.id, "i": i, "size": size} for i in range(params.get("n", 2))]
