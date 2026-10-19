"""Document chat app (Query-Design)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True)
class QuerySettings:
    """Query-Design §9 and §11 (`ingest.toml` [query])."""

    dag: str = "default"
    node: str = "embed"
    top_k: int = 8
    max_rounds: int = 3
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"

    @property
    def ref(self) -> str:
        return f"{self.dag}/{self.node}"

    @classmethod
    def from_config(cls, config: Any) -> QuerySettings:
        """Defaults, overridden by `config.query` when the config carries a [query] table."""
        raw: Mapping[str, Any] = getattr(config, "query", None) or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})
