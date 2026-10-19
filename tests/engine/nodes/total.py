"""Dict node over a table input."""

from __future__ import annotations

from typing import Any

import pyarrow as pa

from ingest.contract import Doc, NodeContext, only


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> dict[str, Any]:
    table: pa.Table = only(inputs)
    return {"rows": table.num_rows, "size": sum(table.column("size").to_pylist())}
