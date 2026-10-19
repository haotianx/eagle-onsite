"""Content-addressed object store: objects/<sha[:2]>/<sha>.<json|parquet>."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

if TYPE_CHECKING:
    import pyarrow as pa

ObjectKind: TypeAlias = Literal["json", "parquet"]

_KINDS: tuple[str, ...] = ("json", "parquet")
_SHA = re.compile(r"[0-9a-f]{64}")


class ObjectStore:
    """Writes are atomic (temp file in the same directory + os.replace) and idempotent: an object
    is named by the SHA-256 of its serialized bytes, so writing the same content twice is a no-op.
    JSON is serialized canonically (fingerprint.canonical_json)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def put_json(self, obj: Any) -> str:
        """Returns the object's sha."""
        data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return self._write(data, "json")

    def put_table(self, table: pa.Table | list[dict[str, Any]]) -> str:
        """A list of dicts is converted with pyarrow.Table.from_pylist. Returns the sha."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        if isinstance(table, list):
            table = pa.Table.from_pylist(table)
        buf = io.BytesIO()
        pq.write_table(table, buf)
        return self._write(buf.getvalue(), "parquet")

    def put(self, records: Any) -> tuple[str, ObjectKind]:
        """dict → json; list or pyarrow.Table → parquet. Raises TypeError otherwise."""
        import pyarrow as pa

        if isinstance(records, dict):
            return self.put_json(records), "json"
        if isinstance(records, (list, pa.Table)):
            return self.put_table(records), "parquet"
        raise TypeError(f"records must be a dict, list of dicts or pyarrow.Table, not {type(records).__name__}")

    def get_json(self, sha: str) -> Any:
        return json.loads(self.path(sha, "json").read_bytes())

    def get_table(self, sha: str) -> pa.Table:
        import pyarrow.parquet as pq

        return pq.read_table(self.path(sha, "parquet"))

    def get(self, sha: str, kind: ObjectKind) -> Any:
        if kind not in _KINDS:
            raise ValueError(f"unknown object kind {kind!r}")
        return self.get_json(sha) if kind == "json" else self.get_table(sha)

    def path(self, sha: str, kind: ObjectKind) -> Path:
        if not _SHA.fullmatch(sha):
            raise ValueError(f"invalid object sha {sha!r}")
        if kind not in _KINDS:
            raise ValueError(f"unknown object kind {kind!r}")
        return self.root / sha[:2] / f"{sha}.{kind}"

    def exists(self, sha: str, kind: ObjectKind) -> bool:
        return self.path(sha, kind).exists()

    def size(self, sha: str, kind: ObjectKind) -> int:
        return self.path(sha, kind).stat().st_size

    def _write(self, data: bytes, kind: ObjectKind) -> str:
        sha = hashlib.sha256(data).hexdigest()
        dest = self.path(sha, kind)
        if dest.exists():
            return sha
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{sha}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return sha
