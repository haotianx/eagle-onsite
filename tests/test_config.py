from __future__ import annotations

from pathlib import Path

import pytest

from ingest.config import DATA_DIR_ENV, SourceConfig, find_repo_root, load_config


def test_find_repo_root_walks_up(tmp_repo: Path) -> None:
    sub = tmp_repo / "a" / "b"
    sub.mkdir(parents=True)
    assert find_repo_root(sub) == tmp_repo.resolve()


def test_find_repo_root_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        find_repo_root(tmp_path)


def test_load_config(tmp_repo: Path, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    cfg = load_config(tmp_repo)
    assert cfg.repo_root == tmp_repo.resolve()
    assert cfg.data_dir == tmp_repo.resolve() / ".ingest"
    assert cfg.catalog_path == cfg.data_dir / "catalog.sqlite"
    assert cfg.run_dir(3) == cfg.data_dir / "runs" / "3"
    src = cfg.sources["proposals"]
    assert src.name == "proposals"
    assert src.root == fixtures_dir
    assert src.folders == (fixtures_dir,)
    assert src.glob == "**/*.pdf"


def test_folders_and_data_dir_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    (tmp_path / "ingest.toml").write_text('[sources.p]\nfolders = ["/abs/docs", "{data_dir}/uploads", "rel"]\n')
    root = tmp_path.resolve()
    src = load_config(tmp_path).sources["p"]
    assert src.folders == (Path("/abs/docs"), root / ".ingest" / "uploads", root / "rel")
    assert src.root == Path("/abs/docs")

    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "elsewhere"))
    assert load_config(tmp_path).sources["p"].folders[1] == tmp_path / "elsewhere" / "uploads"


def test_source_config_validation() -> None:
    assert SourceConfig("p", root=Path("/a")) == SourceConfig("p", (Path("/a"),))
    with pytest.raises(ValueError):
        SourceConfig("p")
    with pytest.raises(ValueError):
        SourceConfig("p", (Path("/a"),), root=Path("/b"))
    with pytest.raises(ValueError):
        SourceConfig("p", (Path("/a"), Path("/x/up"), Path("/y/up")))


def test_defaults_and_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    (tmp_path / "ingest.toml").write_text('[sources.local]\nroot = "docs"\n')
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    root = tmp_path.resolve()
    assert cfg.data_dir == root / ".ingest"
    assert cfg.sources["local"].root == root / "docs"
    assert cfg.sources["local"].glob == "**/*.pdf"


def test_env_overrides_data_dir(
    tmp_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "elsewhere"))
    assert load_config(tmp_repo).data_dir == tmp_path / "elsewhere"
    monkeypatch.setenv(DATA_DIR_ENV, "rel")
    assert load_config(tmp_repo).data_dir == tmp_repo.resolve() / "rel"


def test_no_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    (tmp_path / "ingest.toml").write_text('data_dir = "/abs/data"\n')
    cfg = load_config(tmp_path)
    assert cfg.sources == {}
    assert cfg.data_dir == Path("/abs/data")


@pytest.mark.parametrize(
    "text",
    [
        "bogus = 1\n",
        "data_dir = 3\n",
        "[sources.x]\nglob = '*.pdf'\n",
        "[sources.x]\nroot = 'a'\nextra = 1\n",
        "[sources.x]\nroot = 'a'\nglob = ''\n",
        "[sources.x]\nroot = 'a'\nfolders = ['b']\n",
        "[sources.x]\nfolders = []\n",
        "[sources.x]\nfolders = 'a'\n",
        "[sources.x]\nfolders = ['a', '']\n",
        "[sources.x]\nfolders = ['a', 'x/up', 'y/up']\n",
        "not toml = = \n",
    ],
)
def test_invalid(tmp_path: Path, text: str) -> None:
    (tmp_path / "ingest.toml").write_text(text)
    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_repo_config_includes_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    cfg = load_config(Path(__file__).parents[1])
    assert cfg.sources["proposals"].folders == (Path("/Users/Shared/proposals"), cfg.uploads_dir)
