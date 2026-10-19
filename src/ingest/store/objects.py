"""Content-addressed object store: objects/<sha[:2]>/<sha>.<json|parquet>."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

if TYPE_CHECKING:
    import pyarrow as pa

ObjectKind: TypeAlias = Literal["json", "parquet"]


class ObjectStore:
    """Writes are atomic (temp file in the same directory + os.replace) and idempotent: an object
    is named by the SHA-256 of its serialized bytes, so writing the same content twice is a no-op.
    JSON is serialized canonically (fingerprint.canonical_json)."""

    def __init__(self, root: Path) -> None:
        raise NotImplementedError

    def put_json(self, obj: Any) -> str:
        """Returns the object's sha."""
        raise NotImplementedError

    def put_table(self, table: pa.Table | list[dict[str, Any]]) -> str:
        """A list of dicts is converted with pyarrow.Table.from_pylist. Returns the sha."""
        raise NotImplementedError

    def put(self, records: Any) -> tuple[str, ObjectKind]:
        """dict → json; list or pyarrow.Table → parquet. Raises TypeError otherwise."""
        raise NotImplementedError

    def get_json(self, sha: str) -> Any:
        raise NotImplementedError

    def get_table(self, sha: str) -> pa.Table:
        raise NotImplementedError

    def get(self, sha: str, kind: ObjectKind) -> Any:
        raise NotImplementedError

    def path(self, sha: str, kind: ObjectKind) -> Path:
        raise NotImplementedError

    def exists(self, sha: str, kind: ObjectKind) -> bool:
        raise NotImplementedError

    def size(self, sha: str, kind: ObjectKind) -> int:
        raise NotImplementedError
