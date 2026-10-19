from __future__ import annotations

from pathlib import Path

import pytest

from ingest.fingerprint import sha256_file
from ingest.scripts import ScriptError, inspect_checks, inspect_script, load_checks, load_script


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


PER_DOC = """
ARTIFACT_TYPE = "chunk"

def setup(params):
    return {"ready": True}

def describe(params):
    return {"model": "m"}

def run(doc, inputs, params, ctx):
    return {"tag": TAG}

TAG = "%s"
"""


def test_inspect_script(tmp_path: Path) -> None:
    assert inspect_script(write(tmp_path / "a.py", PER_DOC % "a")) == []
    assert inspect_script(write(tmp_path / "c.py", "def run_corpus(d, i, p, c): ...\n")) == []
    assert "neither" in inspect_script(write(tmp_path / "n.py", "x = 1\n"))[0]
    both = "def run(*a): ...\ndef run_corpus(*a): ...\n"
    assert "both" in inspect_script(write(tmp_path / "b.py", both))[0]
    nested = "class X:\n    def run(self): ...\n"
    assert inspect_script(write(tmp_path / "x.py", nested))
    bad_type = 'ARTIFACT_TYPE = "vector"\ndef run(*a): ...\n'
    assert "ARTIFACT_TYPE" in inspect_script(write(tmp_path / "t.py", bad_type))[0]
    dynamic = "ARTIFACT_TYPE = str('x')\ndef run(*a): ...\n"
    assert inspect_script(write(tmp_path / "dyn.py", dynamic)) == []
    assert "syntax" in inspect_script(write(tmp_path / "s.py", "def run(:\n"))[0]
    assert inspect_script(tmp_path / "missing.py")


def test_inspect_script_does_not_execute(tmp_path: Path) -> None:
    script = write(tmp_path / "heavy.py", "raise SystemExit('imported')\ndef run(*a): ...\n")
    assert inspect_script(script) == []


def test_inspect_checks(tmp_path: Path) -> None:
    assert inspect_checks(write(tmp_path / "ok.py", "def validate(r, p):\n    return []\n")) == []
    assert inspect_checks(write(tmp_path / "no.py", "def check(r, p): ...\n"))


def test_load_script(tmp_path: Path) -> None:
    path = write(tmp_path / "a.py", PER_DOC % "a")
    s = load_script(path)
    assert s.kind == "per_doc"
    assert s.artifact_type == "chunk"
    assert s.source_hash == sha256_file(path)
    assert s.run is not None and s.run(None, {}, {}, None) == {"tag": "a"}
    assert s.run_corpus is None
    assert s.setup is not None and s.setup({}) == {"ready": True}
    assert s.describe is not None and s.describe({}) == {"model": "m"}


def test_load_corpus_script(tmp_path: Path) -> None:
    s = load_script(
        write(tmp_path / "c.py", "def run_corpus(docs, inputs, params, ctx):\n    return {}\n")
    )
    assert s.kind == "corpus"
    assert s.run is None and s.setup is None and s.describe is None and s.artifact_type is None
    assert s.run_corpus is not None


def test_same_file_name_does_not_collide(tmp_path: Path) -> None:
    a = load_script(write(tmp_path / "x" / "node.py", PER_DOC % "x"))
    b = load_script(write(tmp_path / "y" / "node.py", PER_DOC % "y"))
    assert a.run is not None and b.run is not None
    assert a.run(None, {}, {}, None) == {"tag": "x"}
    assert b.run(None, {}, {}, None) == {"tag": "y"}


def test_reload_after_edit(tmp_path: Path) -> None:
    path = write(tmp_path / "n.py", PER_DOC % "old")
    assert load_script(path).run(None, {}, {}, None) == {"tag": "old"}  # type: ignore[misc]
    path.write_text(PER_DOC % "new")
    assert load_script(path).run(None, {}, {}, None) == {"tag": "new"}  # type: ignore[misc]


@pytest.mark.parametrize(
    "text",
    [
        "x = 1\n",
        "def run(*a): ...\ndef run_corpus(*a): ...\n",
        "run = 3\n",
        "ARTIFACT_TYPE = 'vector'\ndef run(*a): ...\n",
        "raise RuntimeError('boom')\n",
        "def run(:\n",
    ],
)
def test_load_script_errors(tmp_path: Path, text: str) -> None:
    with pytest.raises(ScriptError):
        load_script(write(tmp_path / "bad.py", text))


def test_load_script_missing(tmp_path: Path) -> None:
    with pytest.raises(ScriptError):
        load_script(tmp_path / "missing.py")


def test_load_checks(tmp_path: Path) -> None:
    text = (
        "from ingest.contract import Issue\n"
        "def validate(record, params):\n"
        "    return [Issue('warning', 'w', str(record))]\n"
    )
    path = write(tmp_path / "checks.py", text)
    c = load_checks(path)
    assert c.source_hash == sha256_file(path)
    assert c.validate(1, {})[0].message == "1"
    with pytest.raises(ScriptError):
        load_checks(write(tmp_path / "nope.py", "x = 1\n"))
