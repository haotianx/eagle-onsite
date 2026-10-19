from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from ingest.config import Config, SourceConfig, load_config
from ingest.store.catalog import Catalog, PendingDocRow

if TYPE_CHECKING:
    from tests.api.conftest import World

SZEKERES = "2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf"


def test_list_sources(client: TestClient, world: World) -> None:
    [src] = client.get("/api/sources").json()
    assert src["name"] == "proposals" and src["glob"] == "**/*.pdf"
    assert src["folders"] == [str(world.config.sources["proposals"].root)]
    assert src["snapshots"][0] == {
        "id": world.snapshot_id, "source": "proposals", "created_at": src["snapshots"][0]["created_at"],
        "doc_count": 6, "file_count": 7,
    }


def test_sync_unchanged_and_unknown(client: TestClient, world: World) -> None:
    r = client.post("/api/sources/proposals/sync").json()
    assert r["created"] is False and r["snapshot"]["id"] == world.snapshot_id
    assert r["diff"]["added"] == [] and r["diff"]["unchanged"] == 6
    assert client.post("/api/sources/nope/sync").status_code == 404


@pytest.fixture
def copied(tmp_path: Path, world: World, fixtures_dir: Path) -> tuple[Config, Path]:
    """A source over a copy of the fixtures, synced once."""
    root = tmp_path / "copy"
    shutil.copytree(fixtures_dir, root)
    repo = world.config.repo_root
    (repo / "ingest.toml").write_text(f'data_dir = ".ingest"\n\n[sources.proposals]\nroot = "{root}"\n')
    return load_config(repo), root


def test_sync_diff_and_snapshot_docs(world: World, copied: tuple[Config, Path]) -> None:
    from ingest.api.app import create_app

    config, root = copied
    with TestClient(create_app(config)) as client:
        first = client.post("/api/sources/proposals/sync").json()
        assert first["created"] is False
        (root / "3002 ELLIOT/CONTRACT/Mike Proposal_Elliot Pickle Ball.pdf").unlink()
        (root / "9999 NEW").mkdir()
        shutil.copy(root / SZEKERES, root / "9999 NEW/copy.pdf")
        with open(root / SZEKERES, "ab") as f:
            f.write(b"\n%changed\n")
        second = client.post("/api/sources/proposals/sync").json()
        assert second["created"] is True
        diff = second["diff"]
        assert diff["old"]["id"] == world.snapshot_id and diff["new"]["id"] == second["snapshot"]["id"]
        assert sorted(d["filename"] for d in diff["added"]) == ["2292 Szekeres Proposal.pdf"]
        assert sorted(d["filename"] for d in diff["removed"]) == ["Mike Proposal_Elliot Pickle Ball.pdf"]
        assert diff["changed_paths"] == [SZEKERES]
        new_id = second["snapshot"]["id"]
        assert client.get(f"/api/snapshots/{new_id}/diff").json() == diff
        first_diff = client.get(f"/api/snapshots/{world.snapshot_id}/diff").json()
        assert first_diff["old"] is None and len(first_diff["added"]) == 6
        back = client.get(f"/api/snapshots/{world.snapshot_id}/diff", params={"against": new_id}).json()
        assert len(back["added"]) == 1 and len(back["removed"]) == 1
        assert client.get("/api/snapshots/999/diff").status_code == 404
        assert client.get(f"/api/snapshots/{new_id}/diff", params={"against": 999}).status_code == 404

        docs = client.get(f"/api/snapshots/{world.snapshot_id}/docs").json()
        assert len(docs) == 6
        yardley = [d for d in docs if len(d["paths"]) == 2]
        assert yardley and yardley[0]["filename"] == "STAKING PROPOSAL.pdf" and yardley[0]["size"] > 0
        assert [d["job_number"] for d in client.get(f"/api/snapshots/{world.snapshot_id}/docs", params={"q": "3426"}).json()] == ["3426"]
        assert len(client.get(f"/api/snapshots/{world.snapshot_id}/docs", params={"limit": 2}).json()) == 2
        assert client.get("/api/snapshots/999/docs").status_code == 404


def test_sync_missing_root(client: TestClient, world: World, tmp_path: Path) -> None:
    sources = dict(world.config.sources)
    sources["gone"] = SourceConfig("gone", root=tmp_path / "gone")
    client.app.state.config = Config(world.config.repo_root, world.config.data_dir, sources)
    assert client.post("/api/sources/gone/sync").status_code == 400


def test_pdf(client: TestClient, world: World) -> None:
    doc = world.docs["2292 Szekeres Proposal.pdf"]
    r = client.get(f"/api/docs/{doc}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.content.startswith(b"%PDF")
    assert client.get("/api/docs/nope/pdf").status_code == 404


def test_pdf_changed_or_missing(world: World, copied: tuple[Config, Path]) -> None:
    from ingest.api.app import create_app

    config, root = copied
    with TestClient(create_app(config)) as client:
        doc = world.docs["2292 Szekeres Proposal.pdf"]
        os.utime(root / SZEKERES, ns=(1, 1))
        assert client.get(f"/api/docs/{doc}/pdf").status_code == 200  # mtime differs, same content
        with open(root / SZEKERES, "ab") as f:
            f.write(b"\n%changed\n")
        r = client.get(f"/api/docs/{doc}/pdf")
        assert r.status_code == 404 and r.json() == {"detail": "Original changed or missing"}
        elliot = world.docs["Mike Proposal_Elliot Pickle Ball.pdf"]
        (root / "3002 ELLIOT/CONTRACT/Mike Proposal_Elliot Pickle Ball.pdf").unlink()
        assert client.get(f"/api/docs/{elliot}/pdf").json() == {"detail": "Original changed or missing"}


def test_pdf_pending_upload_and_removed(client: TestClient, world: World, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ingest.fingerprint import sha256_file

    uploads = world.config.data_dir / "uploads"
    uploads.mkdir(parents=True)
    shutil.copy(fixtures_dir / SZEKERES, uploads / "mine.pdf")
    pending = PendingDocRow(
        doc_id="f" * 64, version_id=world.embed.id, run_id=1, paths=("uploads/mine.pdf",), meta={}, object_sha="0" * 64
    )
    monkeypatch.setattr(Catalog, "pending_docs", lambda self, version_id=None: [pending])
    assert client.get(f"/api/docs/{'f' * 64}/pdf").status_code == 404  # content differs from the id
    real = sha256_file(uploads / "mine.pdf")
    pending_real = PendingDocRow(
        doc_id=real, version_id=world.embed.id, run_id=1, paths=("uploads/mine.pdf",), meta={}, object_sha="0" * 64
    )
    monkeypatch.setattr(Catalog, "pending_docs", lambda self, version_id=None: [pending_real])
    monkeypatch.setattr(Catalog, "snapshot_doc", lambda self, sid, doc_id: None)
    assert client.get(f"/api/docs/{real}/pdf").status_code == 200
    monkeypatch.setattr(Catalog, "removed_docs", lambda self: {real})
    r = client.get(f"/api/docs/{real}/pdf")
    assert r.status_code == 410 and r.json() == {"detail": "Document removed"}


def test_pdf_stubs_treated_as_empty(client: TestClient, world: World, monkeypatch: Any) -> None:
    def stub(*_: Any, **__: Any) -> None:
        raise NotImplementedError

    monkeypatch.setattr(Catalog, "pending_docs", stub)
    monkeypatch.setattr(Catalog, "removed_docs", stub)
    assert client.get(f"/api/docs/{world.docs['2292 Szekeres Proposal.pdf']}/pdf").status_code == 200
    assert client.get("/api/docs/nope/pdf").status_code == 404
