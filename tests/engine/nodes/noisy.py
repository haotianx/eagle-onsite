"""Emits output on every channel the framework captures."""

from __future__ import annotations

import logging
import os
import sys
import warnings
from typing import Any

from ingest.contract import Doc, NodeContext


def setup(params: dict) -> str:
    print("setting up noisy")
    return "state"


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> dict[str, Any]:
    print(f"stdout {doc.id}")
    print(f"stderr {doc.id}", file=sys.stderr)
    logging.getLogger("toy.noisy").info("log %s", doc.id)
    ctx.log.debug("debug %s", doc.id)
    warnings.warn(f"warn {doc.id}")
    os.write(2, f"fd2 {doc.id}\n".encode())
    ctx.debug("note.json", {"doc": doc.id, "state": ctx.state})
    return {"doc": doc.id}
