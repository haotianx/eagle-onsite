"""Opt-in live test (Query-Design §22): calls the Claude API with the repo's configured library,
whose current default/embed version must cover the fixture proposals (ingest run + promote).
Needs a Claude credential (ANTHROPIC_API_KEY or an `ant auth login` profile) and the embedding
model. Run with: QUERY_LIVE=1 uv run pytest tests/query/test_query_live.py"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from ingest.config import load_config
from ingest.query import load_query_config
from ingest.query.agent import Generations
from ingest.query.library import LibraryCache
from ingest.query.store import ChatStore, CitationRow, MessageRow

pytestmark = pytest.mark.skipif(os.environ.get("QUERY_LIVE") != "1", reason="set QUERY_LIVE=1 to call the Claude API")

QUESTION = "What fees are in the Szekeres proposal?"
SZEKERES = "2292 Szekeres Proposal.pdf"
FEE = "1,750.00"


@pytest.fixture(scope="module")
def library() -> Iterator[LibraryCache]:
    config = load_config()
    lib = LibraryCache(config, load_query_config(config))
    lib.load()
    yield lib
    lib.close()


def ask(library: LibraryCache, tmp_path: Path, scope: str | None) -> tuple[MessageRow, list[CitationRow]]:
    config = load_config()
    store = ChatStore(tmp_path / "chat.sqlite")

    async def main() -> tuple[MessageRow, list[CitationRow]]:
        generations = Generations(store, library, load_query_config(config), log_dir=tmp_path / "logs")
        try:
            chat = store.create_chat()
            _, answer = await generations.ask(chat.id, QUESTION, scope=scope)
            events = generations.subscribe(chat.id)
            assert events is not None
            async for _ in events:
                pass
            return store.get_message(answer.id), store.citations(chat.id).get(answer.id, [])
        finally:
            await generations.close()

    try:
        return asyncio.run(main())
    finally:
        store.close()


def test_all_documents_cites_the_szekeres_fee(library: LibraryCache, tmp_path: Path) -> None:
    answer, citations = ask(library, tmp_path, None)
    assert answer.state == "complete", answer.error
    assert FEE in answer.text
    assert any(c.filename == SZEKERES for c in citations)


def test_scoped_to_the_elliot_proposal_finds_nothing(library: LibraryCache, tmp_path: Path) -> None:
    [elliot] = library.list_documents(filename_contains="Elliot Pickle Ball")
    answer, citations = ask(library, tmp_path, elliot.doc_id)
    assert answer.state == "complete", answer.error
    assert FEE not in answer.text
    assert all(c.doc_id == elliot.doc_id for c in citations)
