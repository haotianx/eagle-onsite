from __future__ import annotations

from pathlib import Path

import pytest

from ingest.query.store import ChatStore


@pytest.fixture
def store(tmp_path: Path) -> ChatStore:
    s = ChatStore(tmp_path / "chat.sqlite")
    yield s
    s.close()


def test_chats_order_by_latest_message(store: ChatStore) -> None:
    a = store.create_chat()
    b = store.create_chat()
    assert [c.id for c in store.list_chats()] == [b.id, a.id]
    store.add_message(a.id, "user", [{"role": "user", "content": "hi"}], state="complete", scope=None)
    assert [c.id for c in store.list_chats()] == [a.id, b.id]


def test_rename_and_scope_do_not_reorder(store: ChatStore) -> None:
    a = store.create_chat()
    store.create_chat()
    before = store.get_chat(a.id).updated
    chat = store.update_chat(a.id, title="Szekeres fees", scope="d1")
    assert (chat.title, chat.scope, chat.updated) == ("Szekeres fees", "d1", before)
    assert store.update_chat(a.id, scope=None).scope is None
    assert store.get_chat(a.id).title == "Szekeres fees"


def test_messages_round_trip_and_update(store: ChatStore) -> None:
    chat = store.create_chat()
    content = [{"role": "user", "content": [{"type": "text", "text": "What fees?"}]}]
    q = store.add_message(chat.id, "user", content, state="complete", scope="d1")
    a = store.add_message(chat.id, "assistant", [], state="generating", scope="d1", model="m")
    assert (q.seq, a.seq) == (1, 2)
    assert store.get_message(q.id).content == content
    a = store.update_message(a.id, content=[{"role": "assistant", "content": []}], state="stopped", usage={"input_tokens": 3})
    assert (a.state, a.usage, a.error, a.latency) == ("stopped", {"input_tokens": 3}, None, None)
    assert store.messages_in_state("stopped") == [a]
    assert [m.id for m in store.messages(chat.id)] == [q.id, a.id]


def test_delete_message_cascades(store: ChatStore) -> None:
    chat = store.create_chat()
    m = store.add_message(chat.id, "assistant", [], state="complete", scope=None)
    store.add_search_step(m.id, round=1, tool="search_documents", query="fees", scope=None, passages=[{"chunk_id": "c"}])
    store.add_citation(m.id, n=1, doc_id="d", chunk_id="c", filename="f.pdf", pages=[1], regions=[], cited_text="x")
    store.delete_message(m.id)
    assert store.search_steps(m.id) == [] and store.citations(m.id) == []
    with pytest.raises(KeyError):
        store.get_message(m.id)


def test_steps_and_citations(store: ChatStore) -> None:
    chat = store.create_chat()
    m = store.add_message(chat.id, "assistant", [], state="complete", scope=None)
    step = store.add_search_step(m.id, round=1, tool="search_documents", query="fees", scope="d", passages=[{"chunk_id": "c", "score": 0.5}])
    assert step.passages == [{"chunk_id": "c", "score": 0.5}]
    region = {"page": 1, "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0}
    c = store.add_citation(m.id, n=1, doc_id="d", chunk_id="c", filename="f.pdf", pages=[1, 2], regions=[region], cited_text="t")
    assert (c.pages, c.regions) == ((1, 2), (region,))


def test_uploads_attachments_and_removal(store: ChatStore) -> None:
    chat = store.create_chat()
    u1 = store.add_upload("a.pdf", doc_id="d1", status="processing", removable=True)
    u2 = store.add_upload("b.pdf", doc_id="d2", status="ready", removable=False)
    q1 = store.add_message(chat.id, "user", [], state="complete", scope="d1")
    q2 = store.add_message(chat.id, "user", [], state="complete", scope="d2")
    store.attach(q2.id, [u2.id])
    store.attach(q1.id, [u1.id, u2.id])
    assert [u.id for u in store.attachments(q1.id)] == [u1.id, u2.id]
    assert [u.id for u in store.chat_uploads(chat.id)] == [u1.id, u2.id]
    assert store.update_upload(u1.id, status="ready", run_id=7).run_id == 7
    store.update_chat(chat.id, scope="d1")
    assert store.clear_scope("d1") == [chat.id]
    assert store.get_chat(chat.id).scope is None
    store.mark_doc_removed("d1")
    assert store.get_upload(u1.id).removed
    assert [u.id for u in store.uploads()] == [u2.id]
    assert [u.id for u in store.uploads(include_removed=True)] == [u1.id, u2.id]
    assert store.uploads_for_doc("d2") == [store.get_upload(u2.id)]


def test_delete_chat_keeps_uploads(store: ChatStore) -> None:
    chat = store.create_chat()
    u = store.add_upload("a.pdf", doc_id="d1", status="ready", removable=True)
    q = store.add_message(chat.id, "user", [], state="complete", scope=None)
    store.attach(q.id, [u.id])
    store.delete_chat(chat.id)
    assert store.get_upload(u.id).filename == "a.pdf"
    assert store.list_chats() == []
    with pytest.raises(KeyError):
        store.delete_chat(chat.id)


def test_reopen_persists(tmp_path: Path) -> None:
    s = ChatStore(tmp_path / "chat.sqlite")
    chat = s.create_chat()
    s.close()
    s = ChatStore(tmp_path / "chat.sqlite")
    assert s.get_chat(chat.id).id == chat.id
    s.close()
