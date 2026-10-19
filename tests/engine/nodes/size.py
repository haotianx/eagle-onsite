"""Root node: file size per doc. Params pick docs (by filename substring) to fail on."""

from __future__ import annotations

import os
import time
from typing import Any

from ingest.contract import Doc, NodeContext

DELAY_ENV = "TOY_DELAY"
"""Seconds to sleep per doc; read from the environment so it stays out of the fingerprint."""


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> dict[str, Any]:
    name = doc.paths[0]
    if params.get("raise_on") and params["raise_on"] in name:
        raise ValueError(f"cannot size {name}")
    if params.get("exit_on") and params["exit_on"] in name:
        os._exit(3)
    if params.get("sleep_on") and params["sleep_on"] in name:
        time.sleep(params.get("sleep_s", 60))
    time.sleep(float(os.environ.get(DELAY_ENV, "0")))
    return {"size": doc.file.stat().st_size, "salt": params.get("salt"), "pid": os.getpid()}
