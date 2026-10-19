from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from ingest.query.store import ChatStore, CitationRow, MessageRow


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "chat.sqlite"


@pytest.fixture
def store(path: Path) -> Iterator[ChatStore]:
    s = ChatStore(path)
    yield s
    s.close()


def ask(store: ChatStore, chat_id: str, text: str = "q", **kw: Any) -> MessageRow:
    content = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    return store.add_message(chat_id, role="user", text=text, content=content, state="complete", scope=None, **kw)


def answer(store: ChatStore, chat_id: str) -> MessageRow:
    return store.add_message(
        chat_id, role="assistant", text="", content=[], state="generating", scope="d1", model="m"
    )


def citation(message_id: str, n: int, **kw: Any) -> CitationRow:
    args: dict[str, Any] = {
        "message_id": message_id,
        "n": n,
        "source": f"c{n}",
        "doc_id": "d1",
        "chunk_id": f"c{n}",
        "filename": "a.pdf",
        "pages": [n, n + 1],
        "regions": [{"page": n, "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0}],
        "cited_text": f"text {n}",
    }
    args.update(kw)
    return CitationRow(**args)


def upload(store: ChatStore, doc_id: str = "d1", **kw: Any) -> str:
    args: dict[str, Any] = {"filename": f"{doc_id}.pdf", "status": "processing", "removable": True}
    args.update(kw)
    return store.add_upload(doc_id=doc_id, **args).id


def is_utc_iso(ts: str) -> bool:
    return datetime.fromisoformat(ts).utcoffset().total_seconds() == 0


def test_open_creates_parent_dirs_and_wal(store: ChatStore, path: Path) -> None:
    assert path.exists()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


# Chats


def test_chat_crud(store: ChatStore) -> None:
    chat = store.create_chat()
    assert len(chat.id) == 32
    assert chat.title is None and chat.scope is None
    assert chat.created_at == chat.updated_at and is_utc_iso(chat.created_at)
    assert store.get_chat(chat.id) == chat

    updated = store.update_chat(chat.id, title="Roof bids", scope="d1")
    assert (updated.title, updated.scope) == ("Roof bids", "d1")
    assert updated.updated_at > chat.updated_at
    assert store.get_chat(chat.id) == updated

    store.delete_chat(chat.id)
    with pytest.raises(KeyError):
        store.get_chat(chat.id)


def test_update_chat_unset_vs_none(store: ChatStore) -> None:
    chat = store.update_chat(store.create_chat().id, title="t", scope="d1")
    kept = store.update_chat(chat.id, scope=None)
    assert (kept.title, kept.scope) == ("t", None)
    assert kept.updated_at > chat.updated_at
    assert store.update_chat(chat.id, title=None).title is None


def test_list_chats_most_recently_updated_first(store: ChatStore) -> None:
    a, b, c = (store.create_chat().id for _ in range(3))
    assert [x.id for x in store.list_chats()] == [c, b, a]
    store.update_chat(a, title="t")
    assert [x.id for x in store.list_chats()] == [a, c, b]
    ask(store, b)
    assert [x.id for x in store.list_chats()] == [b, a, c]


def test_clear_scope(store: ChatStore) -> None:
    a = store.update_chat(store.create_chat().id, scope="d1")
    b = store.update_chat(store.create_chat().id, scope="d1")
    c = store.update_chat(store.create_chat().id, scope="d2")
    assert sorted(store.clear_scope("d1")) == sorted([a.id, b.id])
    assert store.get_chat(a.id) == replace(a, scope=None)
    assert store.get_chat(b.id).updated_at == b.updated_at
    assert store.get_chat(c.id).scope == "d2"
    assert store.clear_scope("d1") == []


def test_delete_chat_cascades_but_keeps_uploads(store: ChatStore) -> None:
    chat = store.create_chat().id
    other = store.create_chat().id
    u = upload(store)
    m = ask(store, chat, upload_ids=[u])
    store.add_search_step(m.id, round=1, tool="search_documents", query="q", scope=None, passages=[])
    store.add_citations(m.id, [citation(m.id, 1)])
    kept = ask(store, other, upload_ids=[u])

    store.delete_chat(chat)
    with pytest.raises(KeyError):
        store.get_message(m.id)
    assert store.messages(chat) == []
    assert store.search_steps(chat) == {} and store.citations(chat) == {} and store.attachments(chat) == {}
    assert store.get_upload(u).id == u
    assert [r.id for r in store.attachments(other)[kept.id]] == [u]
    with pytest.raises(KeyError):
        store.delete_chat(chat)


# Messages


def test_add_message(store: ChatStore) -> None:
    chat = store.create_chat()
    m = ask(store, chat.id, "What is the bid?")
    assert len(m.id) == 32
    assert (m.chat_id, m.seq, m.role, m.text, m.state) == (chat.id, 1, "user", "What is the bid?", "complete")
    assert m.content == [{"role": "user", "content": [{"type": "text", "text": "What is the bid?"}]}]
    assert (m.error, m.scope, m.model, m.usage, m.ttft_ms, m.total_ms) == (None,) * 6
    assert m.request_ids == []
    assert is_utc_iso(m.created_at)
    assert store.get_chat(chat.id).updated_at == m.created_at > chat.updated_at
    assert store.get_message(m.id) == m

    a = answer(store, chat.id)
    assert (a.seq, a.role, a.state, a.scope, a.model) == (2, "assistant", "generating", "d1", "m")
    assert [x.id for x in store.messages(chat.id)] == [m.id, a.id]


def test_seq_is_per_chat_and_continues_after_delete(store: ChatStore) -> None:
    a, b = store.create_chat().id, store.create_chat().id
    assert [ask(store, a).seq for _ in range(3)] == [1, 2, 3]
    assert ask(store, b).seq == 1
    last = store.messages(a)[-1]
    store.delete_message(last.id)
    assert ask(store, a).seq == 3


def test_add_message_missing_chat_or_upload(store: ChatStore) -> None:
    with pytest.raises(KeyError):
        ask(store, "nope")
    chat = store.create_chat()
    with pytest.raises(KeyError):
        ask(store, chat.id, upload_ids=["nope"])
    assert store.messages(chat.id) == []
    assert store.get_chat(chat.id).updated_at == chat.updated_at


def test_update_message(store: ChatStore) -> None:
    chat = store.create_chat().id
    m = answer(store, chat)
    content = [{"role": "assistant", "content": [{"type": "text", "text": "The bid is $5."}]}]
    u = store.update_message(
        m.id,
        text="The bid is $5.",
        content=content,
        state="complete",
        usage={"input_tokens": 10, "cache_read_input_tokens": 4},
        ttft_ms=120.5,
        total_ms=900.0,
        request_ids=["req_1", "req_2"],
    )
    assert (u.text, u.content, u.state) == ("The bid is $5.", content, "complete")
    assert u.usage == {"input_tokens": 10, "cache_read_input_tokens": 4}
    assert (u.ttft_ms, u.total_ms, u.request_ids) == (120.5, 900.0, ["req_1", "req_2"])
    assert (u.model, u.scope, u.seq, u.created_at) == ("m", "d1", 1, m.created_at)
    assert store.get_message(m.id) == u


def test_update_message_unset_vs_none(store: ChatStore) -> None:
    m = answer(store, store.create_chat().id)
    m = store.update_message(m.id, state="error", error="rate_limit", usage={"input_tokens": 1})
    assert store.update_message(m.id) == m
    cleared = store.update_message(m.id, error=None, model=None, usage=None)
    assert (cleared.error, cleared.model, cleared.usage) == (None, None, None)
    assert (cleared.state, cleared.text) == ("error", "")


def test_delete_message_cascades(store: ChatStore) -> None:
    chat = store.create_chat().id
    u = upload(store)
    q = ask(store, chat, upload_ids=[u])
    a = answer(store, chat)
    store.add_search_step(a.id, round=1, tool="search_documents", query="q", scope=None, passages=[])
    store.add_citations(a.id, [citation(a.id, 1)])

    store.delete_message(a.id)
    assert [m.id for m in store.messages(chat)] == [q.id]
    assert store.search_steps(chat) == {} and store.citations(chat) == {}
    store.delete_message(q.id)
    assert store.attachments(chat) == {}
    assert store.get_upload(u).id == u
    with pytest.raises(KeyError):
        store.delete_message(q.id)


def test_mark_interrupted(store: ChatStore) -> None:
    chat = store.create_chat().id
    q = ask(store, chat)
    a = answer(store, chat)
    b = answer(store, store.create_chat().id)
    assert store.mark_interrupted() == 2
    assert store.get_message(q.id).state == "complete"
    assert store.get_message(a.id).state == "stopped"
    assert store.get_message(b.id).state == "stopped"
    assert store.mark_interrupted() == 0


def test_missing_rows_raise_key_error(store: ChatStore) -> None:
    with pytest.raises(KeyError):
        store.get_chat("nope")
    with pytest.raises(KeyError):
        store.update_chat("nope", title="t")
    with pytest.raises(KeyError):
        store.get_message("nope")
    with pytest.raises(KeyError):
        store.update_message("nope", text="t")
    with pytest.raises(KeyError):
        store.update_message("nope")
    with pytest.raises(KeyError):
        store.get_upload("nope")
    with pytest.raises(KeyError):
        store.update_upload("nope", status="ready")
    with pytest.raises(KeyError):
        store.add_search_step("nope", round=1, tool="t", query=None, scope=None, passages=[])
    with pytest.raises(KeyError):
        store.add_citations("nope", [citation("nope", 1)])


# Search steps and citations


def test_search_steps(store: ChatStore) -> None:
    chat = store.create_chat().id
    q = ask(store, chat)
    a = answer(store, chat)
    passages = [{"chunk_id": "c1", "doc_id": "d1", "pages": [3], "score": 0.5}]
    s1 = store.add_search_step(a.id, round=1, tool="search_documents", query="bid", scope="d1", passages=passages)
    s2 = store.add_search_step(
        a.id, round=2, tool="read_document", query=None, scope=None, passages=[], error="outside scope"
    )
    assert (s1.message_id, s1.round, s1.tool, s1.query, s1.scope, s1.passages, s1.error) == (
        a.id,
        1,
        "search_documents",
        "bid",
        "d1",
        passages,
        None,
    )
    assert s2.id > s1.id and s2.error == "outside scope"
    steps = store.search_steps(chat)
    assert list(steps) == [a.id] and q.id not in steps
    assert steps[a.id] == [s1, s2]
    assert store.search_steps(store.create_chat().id) == {}


def test_citations(store: ChatStore) -> None:
    chat = store.create_chat().id
    a = answer(store, chat)
    b = answer(store, chat)
    store.add_citations(a.id, [citation(a.id, 2), citation(a.id, 1, chunk_id=None, filename=None)])
    store.add_citations(b.id, [citation(b.id, 1)])
    cites = store.citations(chat)
    assert list(cites) == [a.id, b.id]
    assert [c.n for c in cites[a.id]] == [1, 2]
    assert cites[a.id][0] == citation(a.id, 1, chunk_id=None, filename=None)
    assert cites[a.id][1].pages == [2, 3]
    assert cites[a.id][1].regions == [{"page": 2, "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0}]

    store.add_citations(a.id, [citation(a.id, 1, cited_text="replaced")])
    assert [c.cited_text for c in store.citations(chat)[a.id]] == ["replaced", "text 2"]


# Uploads and attachments


def test_upload_crud(store: ChatStore) -> None:
    u = store.add_upload(doc_id="d1", filename="a.pdf", status="processing", removable=True, stage="queued")
    assert len(u.id) == 32
    assert (u.doc_id, u.filename, u.status, u.stage, u.removable, u.removed) == (
        "d1",
        "a.pdf",
        "processing",
        "queued",
        True,
        False,
    )
    assert (u.path, u.pages, u.error, u.run_id) == (None, None, None, None)
    assert is_utc_iso(u.created_at)
    assert store.get_upload(u.id) == u

    v = store.update_upload(u.id, stage="indexing", run_id=7, pages=12)
    assert (v.status, v.stage, v.run_id, v.pages) == ("processing", "indexing", 7, 12)
    w = store.update_upload(u.id, status="failed", stage=None, error="unreadable")
    assert (w.status, w.stage, w.error, w.run_id, w.pages) == ("failed", None, "unreadable", 7, 12)
    assert store.get_upload(u.id) == w

    lib = store.add_upload(doc_id="d2", filename="b.pdf", status="ready", removable=False, path=None, pages=3)
    assert (lib.removable, lib.pages, lib.status) == (False, 3, "ready")
    stored = store.add_upload(doc_id="d3", filename="c.pdf", status="processing", removable=True, path="x/c.pdf")
    assert stored.path == "x/c.pdf"


def test_uploads_for_doc_and_set_doc_removed(store: ChatStore) -> None:
    a = upload(store, "d1")
    b = upload(store, "d1", status="ready")
    c = upload(store, "d2")
    assert [u.id for u in store.uploads_for_doc("d1")] == [a, b]
    assert store.uploads_for_doc("d9") == []

    store.set_doc_removed("d1", True)
    assert all(u.removed for u in store.uploads_for_doc("d1"))
    assert not store.get_upload(c).removed
    store.set_doc_removed("d1", False)
    assert not any(u.removed for u in store.uploads_for_doc("d1"))


def test_processing_uploads(store: ChatStore) -> None:
    a = upload(store, "d1")
    upload(store, "d2", status="ready")
    upload(store, "d3", status="failed")
    removed = upload(store, "d4")
    b = upload(store, "d5")
    store.set_doc_removed("d4", True)
    assert [u.id for u in store.processing_uploads()] == [a, b]
    store.update_upload(a, status="ready")
    assert [u.id for u in store.processing_uploads()] == [b]
    assert removed not in [u.id for u in store.processing_uploads()]


def test_attachments_in_order(store: ChatStore) -> None:
    chat = store.create_chat().id
    a, b, c = upload(store, "d1"), upload(store, "d2"), upload(store, "d3")
    m1 = ask(store, chat, upload_ids=[c, a, b])
    ask(store, chat)
    m3 = ask(store, chat, upload_ids=[b])
    att = store.attachments(chat)
    assert list(att) == [m1.id, m3.id]
    assert [u.id for u in att[m1.id]] == [c, a, b]
    assert att[m1.id][0] == store.get_upload(c)
    assert [u.id for u in att[m3.id]] == [b]


# Transactions, persistence, threads


def test_transaction_rolls_back(store: ChatStore) -> None:
    chat = store.create_chat().id
    with pytest.raises(RuntimeError):
        with store.transaction():
            ask(store, chat)
            raise RuntimeError
    assert store.messages(chat) == []

    with store.transaction():
        ask(store, chat, "kept")
        with pytest.raises(RuntimeError):
            with store.transaction():
                ask(store, chat, "dropped")
                raise RuntimeError
    assert [m.text for m in store.messages(chat)] == ["kept"]


def test_reopen_persists(path: Path) -> None:
    s = ChatStore(path)
    chat = s.update_chat(s.create_chat().id, title="t")
    u = upload(s)
    m = ask(s, chat.id, upload_ids=[u])
    s.add_citations(m.id, [citation(m.id, 1)])
    s.close()

    s = ChatStore(path)
    try:
        assert s.list_chats() == [s.get_chat(chat.id)]
        assert s.messages(chat.id) == [s.get_message(m.id)]
        assert s.get_message(m.id).content == m.content
        assert s.citations(chat.id)[m.id] == [citation(m.id, 1)]
        assert [x.id for x in s.attachments(chat.id)[m.id]] == [u]
    finally:
        s.close()


def test_concurrent_writes(store: ChatStore) -> None:
    chats = [store.create_chat().id for _ in range(4)]
    errors: list[Exception] = []

    def work(chat_id: str) -> None:
        try:
            for _ in range(20):
                m = answer(store, chat_id)
                store.add_search_step(m.id, round=1, tool="t", query="q", scope=None, passages=[])
                store.update_message(m.id, state="complete")
                store.messages(chat_id)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=work, args=(c,)) for c in chats]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    for c in chats:
        assert [m.seq for m in store.messages(c)] == list(range(1, 21))
        assert len(store.search_steps(c)) == 20
