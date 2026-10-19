from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from ingest import sources
from ingest.config import SourceConfig, load_config
from ingest.contract import Doc
from ingest.fingerprint import sha256_file
from ingest.sources import SnapshotDiff, check_pdf, parse_path_meta, scan_source, sync
from ingest.store.catalog import Catalog

SZEKERES = "2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf"
ELLIOT = "3002 ELLIOT/CONTRACT/Mike Proposal_Elliot Pickle Ball.pdf"
YARDLEY = (
    "3257 YARDLEY- ARCHIVE FOLDER/CONTRACT/STAKING PROPOSAL.pdf",
    "3257 YARDLEY/REFERENCE/3257 Yardley - ARCHIVE FOLDER/CONTRACT/STAKING PROPOSAL.pdf",
)


@pytest.fixture
def cat(tmp_path: Path) -> Iterator[Catalog]:
    c = Catalog(tmp_path / "data" / "catalog.sqlite")
    yield c
    c.close()


@pytest.fixture
def src_dir(tmp_path: Path, fixtures_dir: Path) -> Path:
    """A mutable copy of the fixtures."""
    return Path(shutil.copytree(fixtures_dir, tmp_path / "src"))


def source(root: Path) -> SourceConfig:
    return SourceConfig(name="proposals", root=root)


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        (SZEKERES, ("2292", "SZEKERES", "CONTRACT/AEROTECH", "2292 Szekeres Proposal.pdf")),
        ("1934-03 KURTZ/CONTRACT/a.pdf", ("1934-03", "KURTZ", "CONTRACT", "a.pdf")),
        ("4476 - Lewis - Via Rancho P3/CONTRACT/a.pdf", ("4476", "Lewis - Via Rancho P3", "CONTRACT", "a.pdf")),
        (YARDLEY[0], ("3257", "YARDLEY- ARCHIVE FOLDER", "CONTRACT", "STAKING PROPOSAL.pdf")),
        ("Misc Proposals/a.pdf", (None, "Misc Proposals", None, "a.pdf")),
        ("3083/a.pdf", ("3083", None, None, "a.pdf")),
        ("a.pdf", (None, None, None, "a.pdf")),
    ],
)
def test_parse_path_meta(rel: str, expected: tuple[str | None, ...]) -> None:
    assert parse_path_meta(rel) == dict(zip(sources.META_KEYS, expected))


def test_scan_collapses_identical_files(fixtures_dir: Path) -> None:
    docs, files = scan_source(source(fixtures_dir))
    assert files == 7
    assert len(docs) == 6
    assert [d.paths[0] for d in docs] == sorted(d.paths[0] for d in docs)
    (yardley,) = [d for d in docs if len(d.paths) > 1]
    assert yardley.paths == YARDLEY
    assert yardley.doc_id == sha256_file(fixtures_dir / YARDLEY[0])
    assert yardley.meta["project"] == "YARDLEY- ARCHIVE FOLDER"
    assert yardley.mtime_ns == (fixtures_dir / YARDLEY[0]).stat().st_mtime_ns
    assert all(d.flags == () and d.flag_reason is None for d in docs)


def test_scan_matches_uppercase_suffix_and_skips_hidden(src_dir: Path) -> None:
    os.rename(src_dir / ELLIOT, src_dir / ELLIOT.replace(".pdf", ".PDF"))
    (src_dir / ".hidden").mkdir()
    shutil.copy(src_dir / SZEKERES, src_dir / ".hidden" / "x.pdf")
    shutil.copy(src_dir / SZEKERES, src_dir / ".x.pdf")
    (src_dir / "notes.txt").write_text("not a pdf")
    docs, files = scan_source(source(src_dir))
    assert files == 7
    assert ELLIOT.replace(".pdf", ".PDF") in {p for d in docs for p in d.paths}


def test_scan_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_source(source(tmp_path / "missing"))


def test_check_pdf_flags(src_dir: Path, fixtures_dir: Path) -> None:
    data = (fixtures_dir / SZEKERES).read_bytes()
    (src_dir / "truncated.pdf").write_bytes(data[: len(data) // 3])
    (src_dir / "empty.pdf").write_bytes(b"")
    assert check_pdf(fixtures_dir / SZEKERES) == ((), None)
    assert check_pdf(src_dir / "truncated.pdf")[0] == ("corrupt",)
    assert check_pdf(src_dir / "empty.pdf")[0] == ("empty",)

    docs, files = scan_source(source(src_dir))
    assert files == 9
    flagged = {d.paths[0]: (d.flags, d.flag_reason) for d in docs if d.flags}
    assert set(flagged) == {"truncated.pdf", "empty.pdf"}
    assert flagged["truncated.pdf"][0] == ("corrupt",) and flagged["truncated.pdf"][1]
    assert flagged["empty.pdf"][0] == ("empty",) and flagged["empty.pdf"][1]


def encrypted_pdf() -> bytes:
    """A minimal PDF using the standard security handler with an unknown user password."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [] /Count 0 >>",
        b"<< /Filter /Standard /V 1 /R 2 /O <" + b"11" * 32 + b"> /U <" + b"22" * 32 + b"> /P -4 >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += b"xref\n0 4\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    file_id = b"<" + b"33" * 16 + b">"
    out += b"trailer\n<< /Size 4 /Root 1 0 R /Encrypt 3 0 R /ID [%s %s] >>\n" % (file_id, file_id)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def test_check_pdf_encrypted(tmp_path: Path) -> None:
    (tmp_path / "locked.pdf").write_bytes(encrypted_pdf())
    flags, reason = check_pdf(tmp_path / "locked.pdf")
    assert flags == ("encrypted",) and reason


def test_sync_unchanged_resync(cat: Catalog, fixtures_dir: Path) -> None:
    first = sync(cat, source(fixtures_dir))
    assert first.created
    assert len(first.diff.added) == 6 and not first.diff.removed
    snap = cat.get_snapshot(first.snapshot_id)
    assert (snap.doc_count, snap.file_count) == (6, 7)

    again = sync(cat, source(fixtures_dir))
    assert again == sources.SyncResult(first.snapshot_id, False, SnapshotDiff((), (), (), 6))
    assert again.diff.empty
    assert len(cat.list_snapshots("proposals")) == 1


def test_sync_reflects_changes(cat: Catalog, src_dir: Path, fixtures_dir: Path) -> None:
    first = sync(cat, source(src_dir))
    old = {d.paths[0]: d.doc_id for d in cat.snapshot_docs(first.snapshot_id)}

    (src_dir / ELLIOT).unlink()
    with open(src_dir / SZEKERES, "ab") as f:
        f.write(b"\n%modified\n")
    shutil.copy(fixtures_dir / ELLIOT, src_dir / "new.pdf")
    shutil.copy(fixtures_dir / YARDLEY[0], src_dir / "yardley copy.pdf")

    second = sync(cat, source(src_dir))
    assert second.created and second.snapshot_id != first.snapshot_id
    new = {p: d.doc_id for d in cat.snapshot_docs(second.snapshot_id) for p in d.paths}
    diff = second.diff
    assert diff.added == (new[SZEKERES],)
    assert diff.removed == (old[SZEKERES],)
    assert diff.changed_paths == (SZEKERES,)
    assert diff.unchanged == 5
    assert new["new.pdf"] == old[ELLIOT] and ELLIOT not in new
    assert sources.diff_snapshots(cat, first.snapshot_id, second.snapshot_id) == diff
    assert cat.snapshot_doc(second.snapshot_id, new[YARDLEY[0]]).paths == (*YARDLEY, "yardley copy.pdf")


def test_sync_new_snapshot_on_path_change_only(cat: Catalog, src_dir: Path) -> None:
    first = sync(cat, source(src_dir))
    os.rename(src_dir / ELLIOT, src_dir / "renamed.pdf")
    second = sync(cat, source(src_dir))
    assert second.created and second.snapshot_id != first.snapshot_id
    assert second.diff.empty


def test_file_index_avoids_rehashing(cat: Catalog, src_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sync(cat, source(src_dir))
    index = cat.file_index_get("proposals")
    assert len(index) == 7
    assert index[SZEKERES] == (
        (src_dir / SZEKERES).stat().st_size,
        (src_dir / SZEKERES).stat().st_mtime_ns,
        sha256_file(src_dir / SZEKERES),
    )

    hashed: list[Path] = []

    def spy(path: Path) -> str:
        hashed.append(path)
        return sha256_file(path)

    monkeypatch.setattr(sources, "sha256_file", spy)
    assert not sync(cat, source(src_dir)).created
    assert hashed == []

    with open(src_dir / SZEKERES, "ab") as f:
        f.write(b"\n")
    assert sync(cat, source(src_dir)).created
    assert hashed == [src_dir / SZEKERES]
    assert cat.file_index_get("proposals")[SZEKERES][2] == sha256_file(src_dir / SZEKERES)


def test_to_doc_and_snapshot_docs(cat: Catalog, tmp_repo: Path, fixtures_dir: Path) -> None:
    src = load_config(tmp_repo).sources["proposals"]
    result = sync(cat, src)
    docs = sources.snapshot_docs(cat, result.snapshot_id, src)
    assert len(docs) == 6 and all(isinstance(d, Doc) for d in docs)
    for d in docs:
        assert d.file.is_absolute() and d.file.is_file()
        assert d.file == fixtures_dir / d.paths[0]
        assert d.id == sha256_file(d.file)
    (yardley,) = [d for d in docs if len(d.paths) > 1]
    assert yardley.paths == YARDLEY
    row = cat.snapshot_doc(result.snapshot_id, yardley.id)
    assert sources.to_doc(row, src) == yardley


def test_doc_meta_columns(cat: Catalog, fixtures_dir: Path) -> None:
    result = sync(cat, source(fixtures_dir))
    row = next(r for r in cat.snapshot_docs(result.snapshot_id) if r.paths[0] == SZEKERES)
    assert sources.doc_meta_columns(row) == {
        "paths": [SZEKERES],
        "job_number": "2292",
        "project": "SZEKERES",
        "subfolder": "CONTRACT/AEROTECH",
        "filename": "2292 Szekeres Proposal.pdf",
    }
