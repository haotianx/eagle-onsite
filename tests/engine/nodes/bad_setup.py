from __future__ import annotations

from typing import Any


def setup(params: dict) -> None:
    raise RuntimeError("model weights not found")


def run(doc: Any, inputs: dict[str, Any], params: dict, ctx: Any) -> dict[str, Any]:
    return {}
