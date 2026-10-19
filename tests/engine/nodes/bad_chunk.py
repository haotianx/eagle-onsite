"""Declares the chunk type but returns rows missing most chunk columns (hard-check errors)."""

from __future__ import annotations

from typing import Any

from ingest.contract import Doc, NodeContext

ARTIFACT_TYPE = "chunk"


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> list[dict[str, Any]]:
    return [{"doc_id": doc.id, "text": "x"}]
