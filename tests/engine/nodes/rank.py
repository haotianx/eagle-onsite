"""Corpus node: rank docs by size; every doc's output depends on the whole doc set."""

from __future__ import annotations

from typing import Any

from ingest.contract import Doc, NodeContext


def run_corpus(docs: list[Doc], inputs: dict[str, dict[str, Any]], params: dict, ctx: NodeContext) -> dict[str, Any]:
    sizes = inputs["size"]
    order = sorted(sizes, key=lambda d: (sizes[d]["size"], d))
    return {d: {"rank": order.index(d), "of": len(order), "size": sizes[d]["size"]} for d in sizes}
