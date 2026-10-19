"""Table node over two inputs: the rows table plus the size record's salt."""

from __future__ import annotations

from typing import Any

import pyarrow as pa

from ingest.contract import Doc, NodeContext


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> pa.Table:
    rows: pa.Table = inputs["rows"]
    return rows.append_column("salt", pa.array([inputs["size"]["salt"]] * rows.num_rows, pa.int64()))
