from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ingest.config import Config
from ingest.query import api, chat_db_path
from ingest.query.agent import ChatBusy, ChatEvent, LibraryNotReady
from ingest.query.library import DocumentInfo, Passage
from ingest.query.store import ChatStore, CitationRow
from ingest.query.uploads import MAX_BYTES, UploadError

PDF = b"%PDF-1.7\n"


def doc_info(doc_id: str, filename: str, *, upload: bool = False) -> DocumentInfo:
    return DocumentInfo(
        doc_id=doc_id,
        paths=(f"proposals/{filename}",),
        filename=filename,
        job_number="2292",
        project="SZEKERES",
        subfolder=None,
        pages=2,
        upload=upload,
        pending=False,
    )


class FakeLibrary:
    def __init__(self, config: Config, qconfig: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.ready = False
        self.has_version = True
        self.docs: dict[str, DocumentInfo] = {}
        self.warmed = False
        calls.append("library.init")

    def load(self) -> None:
        if not self.has_version:
            raise LookupError("no current version")
        self.ready = True

    def warm(self) -> None:
        self.warmed = True

    def document(self, doc_id: str) -> DocumentInfo | None:
        return self.docs.get(doc_id)

    def contains(self, doc_id: str) -> bool:
        return doc_id in self.docs

    def close(self) -> None:
        calls.append("library.close")


class FakeUploads:
    def __init__(
        self, config: Config, qconfig: Any, store: ChatStore, library: FakeLibrary, **kwargs: Any
    ) -> None:
        self.store = store
        self.library = library
        self.kwargs = kwargs
        self.received: list[tuple[str, int]] = []
        self.snapshots: dict[str, list[dict[str, Any]]] = {}

    async def start(self) -> None:
        calls.append("uploads.start")

    async def close(self) -> None:
        calls.append("uploads.close")

    async def add(self, filename: str, data: bytes) -> Any:
        self.received.append((filename, len(data)))
        error = "Over 50 MB" if len(data) > MAX_BYTES else None if data.startswith(b"%PDF") else "Not a PDF"
        if error:
            return self.store.add_upload(doc_id="", filename=filename, status="failed", removable=False, error=error)
        return self.store.add_upload(
            doc_id="d-up", filename=filename, status="processing", removable=True, stage="queued"
        )

    def get(self, upload_id: str) -> Any:
        return self.store.get_upload(upload_id)

    async def events(self, upload_id: str) -> AsyncIterator[dict[str, Any]]:
        self.store.get_upload(upload_id)
        for snapshot in self.snapshots.get(upload_id, []):
            yield snapshot

    async def remove(self, upload_id: str) -> Any:
        row = self.store.get_upload(upload_id)
        if not row.removable:
            raise UploadError("Only uploads can be removed")
        self.store.set_doc_removed(row.doc_id, True)
        return self.store.get_upload(upload_id)


SCRIPT = [
    ("search_started", {"round": 1, "tool": "search_documents", "query": "fees", "scope": None}),
    ("text_delta", {"text": "The fee is $1,750.00"}),
    ("citation", {"n": 1, "source": "c1", "doc_id": "d1", "chunk_id": "c1", "filename": "a.pdf",
                  "pages": [1], "regions": [], "cited_text": "$1,750.00"}),
]


class FakeGenerations:
    """Answers with SCRIPT plus `done`; the answer stays active until finish()."""

    def __init__(
        self, store: ChatStore, library: FakeLibrary, qconfig: Any, *, log_dir: Path, client_factory: Any
    ) -> None:
        self.store = store
        self.library = library
        self.client_factory = client_factory
        self.active: dict[str, list[ChatEvent]] = {}
        self.stopped: list[str] = []
        self.cancelled: list[str] = []

    def generating(self, chat_id: str) -> bool:
        return chat_id in self.active

    def _start(self, chat_id: str, scope: str | None) -> Any:
        answer = self.store.add_message(
            chat_id, role="assistant", text="", content=[], state="generating", scope=scope
        )
        events = [(name, data) for name, data in SCRIPT]
        events.append(("done", {"message_id": answer.id, "state": "complete", "usage": {}}))
        self.active[chat_id] = [ChatEvent(i, name, data) for i, (name, data) in enumerate(events, 1)]
        return answer

    async def ask(self, chat_id: str, text: str, *, scope: str | None, upload_ids: Any = ()) -> Any:
        self.store.get_chat(chat_id)
        if chat_id in self.active:
            raise ChatBusy(chat_id)
        if not self.library.ready:
            raise LibraryNotReady()
        if scope is not None and not self.library.contains(scope):
            raise ValueError("Not in the library")
        question = self.store.add_message(
            chat_id, role="user", text=text, content=[], state="complete", scope=scope, upload_ids=upload_ids
        )
        return question, self._start(chat_id, scope)

    async def retry(self, chat_id: str) -> Any:
        self.store.get_chat(chat_id)
        if chat_id in self.active:
            raise ChatBusy(chat_id)
        answers = [m for m in self.store.messages(chat_id) if m.role == "assistant"]
        if not answers:
            raise LookupError("No answer to retry")
        self.store.delete_message(answers[-1].id)
        return self._start(chat_id, answers[-1].scope)

    async def stop(self, chat_id: str) -> None:
        self.stopped.append(chat_id)
        self.finish(chat_id)

    def finish(self, chat_id: str) -> None:
        self.active.pop(chat_id, None)

    def subscribe(self, chat_id: str) -> AsyncIterator[ChatEvent] | None:
        if chat_id not in self.active:
            return None

        async def events() -> AsyncIterator[ChatEvent]:
            for event in list(self.active[chat_id]):
                yield event

        return events()

    async def cancel_chat(self, chat_id: str) -> None:
        self.cancelled.append(chat_id)
        self.finish(chat_id)

    async def close(self) -> None:
        calls.append("generations.close")


def fake_answer_parts(message: Any, citations: Any) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": message.text}]
    parts += [{"type": "citation", "n": c.n} for c in citations]
    return parts


calls: list[str] = []


@pytest.fixture
def services_patched(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    calls.clear()
    monkeypatch.setattr(api, "LibraryCache", FakeLibrary)
    monkeypatch.setattr(api, "Uploads", FakeUploads)
    monkeypatch.setattr(api, "Generations", FakeGenerations)
    monkeypatch.setattr(api, "answer_parts", fake_answer_parts)
    yield


def make_app(config: Config, **hooks: Any) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        calls.append("app.start")
        async with api.lifespan(app, config, **hooks):
            yield
        calls.append("app.stop")

    app = FastAPI(lifespan=lifespan)
    app.include_router(api.router, prefix="/api")
    return app


@pytest.fixture
def client(config: Config, services_patched: None) -> Iterator[TestClient]:
    with TestClient(make_app(config)) as c:
        yield c


def services(client: TestClient) -> api.QueryServices:
    return client.app.state.query  # type: ignore[attr-defined]


def sse(text: str) -> list[dict[str, str]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        events.append(fields)
    return events


def test_lifespan_starts_and_stops_services(config: Config, services_patched: None) -> None:
    app = make_app(config)
    with TestClient(app) as c:
        assert calls == ["app.start", "library.init", "uploads.start"]
        s = services(c)
        assert isinstance(s.store, ChatStore)
        assert s.library.ready
        assert chat_db_path(config).exists()
    assert calls[-4:] == ["generations.close", "uploads.close", "library.close", "app.stop"]
    assert not hasattr(app.state, "query")


def test_lifespan_passes_factories(config: Config, services_patched: None) -> None:
    client_factory, runner_factory, embedder_factory = object(), object(), object()
    app = make_app(
        config, client_factory=client_factory, runner_factory=runner_factory, embedder_factory=embedder_factory
    )
    with TestClient(app) as c:
        s = services(c)
        assert s.library.kwargs == {"embedder_factory": embedder_factory}
        assert s.uploads.kwargs == {"runner_factory": runner_factory}
        assert s.generations.client_factory is client_factory


def test_startup_marks_interrupted_answers(config: Config, services_patched: None) -> None:
    store = ChatStore(chat_db_path(config))
    chat = store.create_chat()
    store.add_message(chat.id, role="assistant", text="partial", content=[], state="generating", scope=None)
    store.close()
    with TestClient(make_app(config)) as c:
        messages = c.get(f"/api/chat/chats/{chat.id}").json()["messages"]
    assert messages[0]["state"] == "stopped"


def test_library_not_ready(config: Config, services_patched: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeLibrary, "load", lambda self: (_ for _ in ()).throw(LookupError("none")))
    with TestClient(make_app(config)) as c:
        assert c.get("/api/chat/chats").json() == {"library_ready": False, "chats": []}
        chat = c.post("/api/chat/chats").json()
        r = c.post(f"/api/chat/chats/{chat['id']}/messages", json={"text": "Fees?"})
        assert r.status_code == 409
        assert r.json()["detail"] == "Library isn't ready"


def test_chat_crud(client: TestClient) -> None:
    a = client.post("/api/chat/chats").json()
    b = client.post("/api/chat/chats").json()
    assert set(a) == {"id", "title", "updated_at", "generating"}
    assert a["title"] is None and not a["generating"]

    r = client.patch(f"/api/chat/chats/{a['id']}", json={"title": "  Szekeres fees "})
    assert r.json()["title"] == "Szekeres fees"
    assert client.patch(f"/api/chat/chats/{a['id']}", json={"title": " "}).status_code == 422

    listing = client.get("/api/chat/chats").json()
    assert listing["library_ready"] is True
    assert [c["id"] for c in listing["chats"]] == [a["id"], b["id"]]

    detail = client.get(f"/api/chat/chats/{b['id']}").json()
    assert detail == {"id": b["id"], "title": None, "scope": None, "generating": False, "messages": []}

    assert client.delete(f"/api/chat/chats/{b['id']}").status_code == 204
    assert client.get(f"/api/chat/chats/{b['id']}").status_code == 404
    assert client.delete(f"/api/chat/chats/{b['id']}").status_code == 404
    assert client.patch("/api/chat/chats/nope", json={"title": "x"}).status_code == 404


def test_ask_streams_events_and_replays(client: TestClient) -> None:
    chat = client.post("/api/chat/chats").json()
    r = client.post(f"/api/chat/chats/{chat['id']}/messages", json={"text": "Fees?", "scope": None, "upload_ids": []})
    assert r.status_code == 202
    body = r.json()
    assert body["question"]["role"] == "user" and body["question"]["text"] == "Fees?"
    assert body["answer"]["role"] == "assistant" and body["answer"]["state"] == "generating"
    assert client.get("/api/chat/chats").json()["chats"][0]["generating"] is True

    busy = client.post(f"/api/chat/chats/{chat['id']}/messages", json={"text": "Again"})
    assert busy.status_code == 409 and busy.json()["detail"] == "An answer is already generating"

    for _ in range(2):
        r = client.get(f"/api/chat/chats/{chat['id']}/events")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = sse(r.text)
        assert [e["event"] for e in events] == ["search_started", "text_delta", "citation", "done"]
        assert [e["id"] for e in events] == ["1", "2", "3", "4"]
        assert events[1]["data"] == '{"text": "The fee is $1,750.00"}'
        assert '"message_id": "' + body["answer"]["id"] in events[3]["data"]

    services(client).generations.finish(chat["id"])
    r = client.get(f"/api/chat/chats/{chat['id']}/events")
    assert r.status_code == 204 and r.content == b""
    assert client.get("/api/chat/chats/nope/events").status_code == 404


def test_ask_errors(client: TestClient) -> None:
    chat = client.post("/api/chat/chats").json()
    r = client.post(f"/api/chat/chats/{chat['id']}/messages", json={"text": "Fees?", "scope": "missing"})
    assert r.status_code == 409 and r.json()["detail"] == "Not in the library"
    assert client.post("/api/chat/chats/nope/messages", json={"text": "Fees?"}).status_code == 404
    assert client.post(f"/api/chat/chats/{chat['id']}/messages", json={"text": ""}).status_code == 422


def test_retry_and_stop(client: TestClient) -> None:
    chat = client.post("/api/chat/chats").json()
    gens = services(client).generations
    r = client.post(f"/api/chat/chats/{chat['id']}/retry")
    assert r.status_code == 409 and r.json()["detail"] == "No answer to retry"

    first = client.post(f"/api/chat/chats/{chat['id']}/messages", json={"text": "Fees?"}).json()
    assert client.post(f"/api/chat/chats/{chat['id']}/retry").status_code == 409

    assert client.post(f"/api/chat/chats/{chat['id']}/stop").status_code == 204
    assert gens.stopped == [chat["id"]]
    assert client.post("/api/chat/chats/nope/stop").status_code == 404

    r = client.post(f"/api/chat/chats/{chat['id']}/retry")
    assert r.status_code == 202
    answer = r.json()["answer"]
    assert answer["id"] != first["answer"]["id"] and answer["state"] == "generating"
    ids = [m["id"] for m in client.get(f"/api/chat/chats/{chat['id']}").json()["messages"]]
    assert ids == [first["question"]["id"], answer["id"]]


def test_delete_stops_generation(client: TestClient) -> None:
    chat = client.post("/api/chat/chats").json()
    client.post(f"/api/chat/chats/{chat['id']}/messages", json={"text": "Fees?"})
    assert client.delete(f"/api/chat/chats/{chat['id']}").status_code == 204
    gens = services(client).generations
    assert gens.cancelled == [chat["id"]]
    assert client.get("/api/chat/chats").json()["chats"] == []


def test_upload_routes(client: TestClient) -> None:
    s = services(client)
    s.library.docs["d-up"] = doc_info("d-up", "Addendum.pdf", upload=True)

    r = client.post("/api/chat/uploads", files={"file": ("Addendum.pdf", PDF + b"x" * 100, "application/pdf")})
    assert r.status_code == 200
    card = r.json()
    assert card == {
        "id": card["id"], "doc_id": "d-up", "filename": "Addendum.pdf", "pages": None,
        "status": "processing", "stage": "queued", "error": None, "removable": True,
        "removed": False, "job_number": "2292", "project": "SZEKERES",
    }

    snapshots = [
        {"id": card["id"], "doc_id": "d-up", "status": "processing", "stage": "reading", "pages": 2, "error": None},
        {"id": card["id"], "doc_id": "d-up", "status": "ready", "stage": None, "pages": 2, "error": None},
    ]
    s.uploads.snapshots[card["id"]] = snapshots
    r = client.get(f"/api/chat/uploads/{card['id']}/events")
    assert r.status_code == 200
    events = sse(r.text)
    assert [e["event"] for e in events] == ["status", "status"]
    assert '"stage": "reading"' in events[0]["data"] and '"status": "ready"' in events[1]["data"]
    assert client.get("/api/chat/uploads/nope/events").status_code == 404

    r = client.delete(f"/api/chat/uploads/{card['id']}")
    assert r.status_code == 200 and r.json()["removed"] is True
    assert client.delete("/api/chat/uploads/nope").status_code == 404

    proposal = s.store.add_upload(doc_id="d1", filename="a.pdf", status="ready", removable=False)
    r = client.delete(f"/api/chat/uploads/{proposal.id}")
    assert r.status_code == 409 and r.json()["detail"] == "Only uploads can be removed"


def test_upload_failures(client: TestClient) -> None:
    r = client.post("/api/chat/uploads", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 200
    assert r.json()["status"] == "failed" and r.json()["error"] == "Not a PDF"

    r = client.post("/api/chat/uploads", files={"file": ("big.pdf", PDF + b"x" * MAX_BYTES, "application/pdf")})
    assert r.status_code == 200
    assert r.json()["status"] == "failed" and r.json()["error"] == "Over 50 MB"
    assert services(client).uploads.received[-1] == ("big.pdf", MAX_BYTES + 1)


def test_chat_detail_shape(client: TestClient) -> None:
    s = services(client)
    store = s.store
    s.library.docs["d1"] = doc_info("d1", "2292 Szekeres Proposal.pdf")
    region = {"page": 1, "x0": 10.0, "y0": 20.0, "x1": 300.0, "y1": 60.0}
    passage = Passage(
        chunk_id="c1", doc_id="d1", seq=0, text="Fee $1,750.00", pages=(1,), regions=(region,),
        section=("Fees",), filename="2292 Szekeres Proposal.pdf", job_number="2292", project="SZEKERES", score=0.8,
    )

    up = store.add_upload(doc_id="d-gone", filename="Addendum.pdf", status="ready", removable=True, pages=3)
    store.set_doc_removed("d-gone", True)
    up = store.get_upload(up.id)

    chat = store.create_chat()
    store.update_chat(chat.id, scope="d1")
    q = store.add_message(
        chat.id, role="user", text="Fees?", content=[], state="complete", scope="d1", upload_ids=[up.id]
    )
    a = store.add_message(
        chat.id, role="assistant", text="The fee is $1,750.00", content=[], state="complete", scope="d1"
    )
    store.add_search_step(
        a.id, round=1, tool="search_documents", query="fees", scope="d1", passages=[passage.to_dict()]
    )
    store.add_search_step(a.id, round=2, tool="list_documents", query="job_number=2292", scope=None,
                          passages=[doc_info("d1", "2292 Szekeres Proposal.pdf").to_dict()])
    store.add_search_step(
        a.id, round=3, tool="read_document", query=None, scope=None, passages=[], error="Not available"
    )
    store.add_citations(a.id, [
        CitationRow(
            message_id=a.id, n=1, source="c1", doc_id="d1", chunk_id="c1",
            filename="2292 Szekeres Proposal.pdf", pages=[1], regions=[region], cited_text="Fee $1,750.00",
        ),
        CitationRow(
            message_id=a.id, n=2, source="c9", doc_id="d-gone", chunk_id="c9",
            filename="Addendum.pdf", pages=[2], regions=[], cited_text="Addendum",
        ),
    ])

    detail = client.get(f"/api/chat/chats/{chat.id}").json()
    assert detail["scope"] == {"doc_id": "d1", "filename": "2292 Szekeres Proposal.pdf"}
    question, answer = detail["messages"]
    assert question == {
        "id": q.id, "role": "user", "text": "Fees?", "created_at": q.created_at,
        "scope": {"doc_id": "d1", "filename": "2292 Szekeres Proposal.pdf"},
        "attachments": [{
            "id": up.id, "doc_id": "d-gone", "filename": "Addendum.pdf", "pages": 3, "status": "ready",
            "stage": None, "error": None, "removable": True, "removed": True, "job_number": None, "project": None,
        }],
    }
    assert set(answer) == {"id", "role", "state", "error", "scope", "created_at", "parts", "search_steps", "citations"}
    assert answer["state"] == "complete" and answer["error"] is None
    assert answer["parts"] == [{"type": "text", "text": "The fee is $1,750.00"}, {"type": "citation", "n": 1},
                               {"type": "citation", "n": 2}]
    steps = answer["search_steps"]
    assert [st["tool"] for st in steps] == ["search_documents", "list_documents", "read_document"]
    assert steps[0]["passages"][0] == passage.to_dict() | {"regions": [region]}
    assert steps[1]["passages"][0]["paths"] == ["proposals/2292 Szekeres Proposal.pdf"]
    assert steps[2]["error"] == "Not available"
    assert answer["citations"] == [
        {"n": 1, "doc_id": "d1", "chunk_id": "c1", "filename": "2292 Szekeres Proposal.pdf", "pages": [1],
         "regions": [region], "cited_text": "Fee $1,750.00", "removed": False},
        {"n": 2, "doc_id": "d-gone", "chunk_id": "c9", "filename": "Addendum.pdf", "pages": [2],
         "regions": [], "cited_text": "Addendum", "removed": True},
    ]

    store.update_chat(chat.id, scope="d-gone")
    assert client.get(f"/api/chat/chats/{chat.id}").json()["scope"] == {"doc_id": "d-gone", "filename": "Addendum.pdf"}
