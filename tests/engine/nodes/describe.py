"""Root node with describe()."""

from __future__ import annotations

from typing import Any

from ingest.contract import Doc, NodeContext


def describe(params: dict) -> dict:
    return {"model": "toy", "dim": params.get("dim", 2)}


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> dict[str, Any]:
    return {"id": doc.id}
