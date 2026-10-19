from __future__ import annotations

import os
import threading
from pathlib import Path

from conftest import git

from ingest.gitinfo import dirty_paths, head_commit, is_dirty, materialize_head, watch


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def commit_all(repo: Path) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c")
    return git(repo, "rev-parse", "HEAD").strip()


def test_head_commit(tmp_repo: Path, tmp_path: Path) -> None:
    assert head_commit(tmp_repo) is None
    sha = commit_all(tmp_repo)
    assert head_commit(tmp_repo) == sha
    assert len(sha) == 40
    plain = tmp_path / "plain"
    plain.mkdir()
    assert head_commit(plain) is None


def test_dirty_paths_without_commits(tmp_repo: Path) -> None:
    write(tmp_repo / "nodes" / "a.py", "x")
    write(tmp_repo / ".ingest" / "catalog.sqlite", "ignored")
    git(tmp_repo, "add", "ingest.toml")
    assert dirty_paths(tmp_repo) == {"ingest.toml", ".gitignore", "nodes/a.py"}


def test_dirty_paths(tmp_repo: Path) -> None:
    write(tmp_repo / "nodes" / "a.py", "a")
    write(tmp_repo / "nodes" / "b.py", "b")
    write(tmp_repo / "nodes" / "c.py", "c")
    write(tmp_repo / "nodes" / "d.py", "d")
    commit_all(tmp_repo)
    assert dirty_paths(tmp_repo) == set()

    write(tmp_repo / "nodes" / "a.py", "a2")
    write(tmp_repo / "nodes" / "b.py", "b2")
    git(tmp_repo, "add", "nodes/b.py")
    (tmp_repo / "nodes" / "c.py").unlink()
    git(tmp_repo, "mv", "nodes/d.py", "nodes/e.py")
    write(tmp_repo / "new dir" / "f ü.py", "f")
    write(tmp_repo / ".ingest" / "x", "ignored")
    assert dirty_paths(tmp_repo) == {
        "nodes/a.py",
        "nodes/b.py",
        "nodes/c.py",
        "nodes/d.py",
        "nodes/e.py",
        "new dir/f ü.py",
    }
    assert is_dirty(tmp_repo, ["nodes/a.py", "dags/x.yaml"])
    assert is_dirty(tmp_repo, ["./nodes/c.py"])
    assert not is_dirty(tmp_repo, ["ingest.toml"])
    assert not is_dirty(tmp_repo, [])


def test_dirty_paths_from_subdirectory_root(tmp_repo: Path) -> None:
    write(tmp_repo / "sub" / "ingest.toml", "")
    write(tmp_repo / "other.py", "x")
    assert dirty_paths(tmp_repo / "sub") == {"ingest.toml"}


def test_materialize_head(tmp_repo: Path, tmp_path: Path) -> None:
    write(tmp_repo / "dags" / "default.yaml", "name: committed\n")
    write(tmp_repo / "nodes" / "a.py", "a")
    write(tmp_repo / "nodes" / "checks" / "c.py", "c")
    write(tmp_repo / "nodes2" / "z.py", "z")
    (tmp_repo / "nodes" / "run.sh").write_text("#!/bin/sh\n")
    (tmp_repo / "nodes" / "run.sh").chmod(0o755)
    commit_all(tmp_repo)
    write(tmp_repo / "dags" / "default.yaml", "name: edited\n")
    write(tmp_repo / "nodes" / "new.py", "untracked")

    dest = tmp_path / "head"
    missing = materialize_head(
        tmp_repo, ["dags/default.yaml", "nodes", "nodes/new.py", "gone"], dest
    )
    assert missing == ["nodes/new.py", "gone"]
    assert (dest / "dags" / "default.yaml").read_text() == "name: committed\n"
    assert (dest / "nodes" / "a.py").read_text() == "a"
    assert (dest / "nodes" / "checks" / "c.py").read_text() == "c"
    assert os.access(dest / "nodes" / "run.sh", os.X_OK)
    assert not (dest / "nodes" / "new.py").exists()
    assert not (dest / "nodes2").exists()
    assert not (dest / "ingest.toml").exists()


def test_materialize_head_without_commits(tmp_repo: Path, tmp_path: Path) -> None:
    assert materialize_head(tmp_repo, ["ingest.toml"], tmp_path / "out") == ["ingest.toml"]


def test_watch(tmp_repo: Path) -> None:
    write(tmp_repo / "nodes" / "a.py", "a")
    stop = threading.Event()
    seen: list[set[str]] = []
    got = threading.Event()

    def callback(changed: set[str]) -> None:
        seen.append(changed)
        got.set()

    t = threading.Thread(
        target=watch, args=(tmp_repo, ["nodes", "dags/x.yaml"], callback, stop), daemon=True
    )
    t.start()
    try:
        for _ in range(50):
            write(tmp_repo / "ingest.toml.other", "unwatched")
            write(tmp_repo / "nodes" / "a.py", "changed")
            if got.wait(0.2):
                break
        assert got.is_set()
        assert set().union(*seen) == {"nodes/a.py"}
    finally:
        stop.set()
        t.join(10)
    assert not t.is_alive()
