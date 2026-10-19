"""Per-doc node that copies its input record."""

from __future__ import annotations

from typing import Any

from ingest.contract import Doc, NodeContext, only


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> dict[str, Any]:
    return dict(only(inputs))
