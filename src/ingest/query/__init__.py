"""Document chat over the library (Query-Design.md).

Modules:
    library.py   in-memory library: scoped search with duplicate collapsing, read, list
    store.py     chat.sqlite: chats, messages, search steps, citations, uploads, attachments
    uploads.py   upload checks, storage, single-doc run queue, progress, removal, reprocessing
    prompts.py   system, scope and title prompts
    agent.py     agent loop, tools, citation mapping, generation tasks
    api.py       mount(app, config): /chat routes + SSE, startup and shutdown
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from ingest.config import CONFIG_FILE, Config


@dataclass(frozen=True)
class QueryConfig:
    """ingest.toml [query] (Query-Design §11); every key is optional."""

    dag: str = "default"
    node: str = "embed"
    top_k: int = 8
    max_rounds: int = 3
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"

    @property
    def ref(self) -> str:
        """The library's version ref, e.g. "default/embed"."""
        return f"{self.dag}/{self.node}"


def load_query_config(config: Config) -> QueryConfig:
    """Read the [query] table of <repo>/ingest.toml; defaults for missing keys (or a missing
    file). Raises ValueError for unknown keys or values of the wrong type."""
    path = config.repo_root / CONFIG_FILE
    if not path.is_file():
        return QueryConfig()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8")).get("query", {})
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: [query] must be a table")
    known = {f.name: f for f in fields(QueryConfig)}
    unknown = set(raw) - set(known)
    if unknown:
        raise ValueError(f"{path}: [query] unknown keys {sorted(unknown)}")
    for key, value in raw.items():
        expected = int if known[key].type in (int, "int") else str
        if not isinstance(value, expected) or isinstance(value, bool):
            raise ValueError(f"{path}: [query] {key} must be {expected.__name__}")
    return QueryConfig(**raw)


def chat_db_path(config: Config) -> Path:
    return config.data_dir / "chat.sqlite"


def chat_logs_dir(config: Config) -> Path:
    return config.data_dir / "chat" / "logs"
