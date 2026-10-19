"""Git state of the repo: HEAD, uncommitted paths, exporting HEAD files, watching."""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath


def _git(
    repo_root: Path, *args: str, input: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args], input=input, capture_output=True, check=False
    )


def _norm(path: str) -> str:
    return PurePosixPath(path).as_posix().removeprefix("./")


def head_commit(repo_root: Path) -> str | None:
    """Full SHA of HEAD, or None if the repo has no commits or isn't a git repo."""
    try:
        r = _git(repo_root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    except OSError:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode().strip() or None


def dirty_paths(repo_root: Path) -> set[str]:
    """Repo-relative POSIX paths that differ from HEAD: modified, staged, deleted, or untracked
    (ignored files excluded)."""
    r = _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--no-renames",
        "--ignore-submodules=none",
    )
    if r.returncode != 0:
        return set()
    prefix = _git(repo_root, "rev-parse", "--show-prefix").stdout.decode().strip()
    out: set[str] = set()
    for entry in r.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if len(entry) < 4:
            continue
        path = entry[3:]
        if path.startswith(prefix):
            out.add(path[len(prefix) :])
    return out


def is_dirty(repo_root: Path, paths: Iterable[str]) -> bool:
    """True if any of `paths` (repo-relative files) is in dirty_paths()."""
    wanted = {_norm(p) for p in paths}
    return bool(wanted) and not wanted.isdisjoint(dirty_paths(repo_root))


def materialize_head(repo_root: Path, paths: Iterable[str], dest: Path) -> list[str]:
    """Write the HEAD version of each path (file or directory) under `dest`, keeping relative
    paths. Returns paths that don't exist in HEAD (skipped)."""
    paths = list(paths)
    if head_commit(repo_root) is None:
        return paths
    missing: list[str] = []
    entries: dict[str, tuple[str, str]] = {}
    for p in paths:
        rel = _norm(p)
        r = _git(repo_root, "ls-tree", "-r", "-z", "HEAD", "--", rel or ".")
        found = False
        for line in r.stdout.decode("utf-8", "surrogateescape").split("\0"):
            if not line:
                continue
            meta, name = line.split("\t", 1)
            mode, kind, sha = meta.split()
            if kind != "blob" or not (
                rel in ("", ".") or name == rel or name.startswith(rel + "/")
            ):
                continue
            entries[name] = (mode, sha)
            found = True
        if r.returncode != 0 or not found:
            missing.append(p)
    if not entries:
        return missing

    names = list(entries)
    shas = "".join(entries[n][1] + "\n" for n in names).encode()
    r = _git(repo_root, "cat-file", "--batch", input=shas)
    if r.returncode != 0:
        raise RuntimeError(f"git cat-file failed: {r.stderr.decode().strip()}")
    data, pos = r.stdout, 0
    for name in names:
        header_end = data.index(b"\n", pos)
        size = int(data[pos:header_end].split()[2])
        content = data[header_end + 1 : header_end + 1 + size]
        pos = header_end + 1 + size + 1
        mode = entries[name][0]
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        if mode == "120000":
            os.symlink(os.fsdecode(content), target)
            continue
        target.write_bytes(content)
        if mode == "100755":
            target.chmod(0o755)
    return missing


def watch(
    repo_root: Path,
    paths: Iterable[str],
    callback: Callable[[set[str]], None],
    stop: threading.Event,
    *,
    debounce_ms: int = 300,
) -> None:
    """Block until `stop` is set, calling `callback(changed repo-relative paths)` whenever files
    under `paths` change (watchfiles)."""
    import watchfiles

    wanted = [_norm(p) for p in paths]
    if not wanted:
        stop.wait()
        return
    roots = {repo_root.resolve(), repo_root.absolute()}

    def rel(path: str) -> str | None:
        p = Path(path)
        for root in roots:
            if p.is_relative_to(root):
                return p.relative_to(root).as_posix()
        return None

    def under(r: str) -> bool:
        return any(w in ("", ".") or r == w or r.startswith(w + "/") for w in wanted)

    for changes in watchfiles.watch(
        repo_root.resolve(),
        stop_event=stop,
        debounce=debounce_ms,
        raise_interrupt=False,
    ):
        changed = {r for _, path in changes if (r := rel(path)) is not None and under(r)}
        if changed:
            callback(changed)
