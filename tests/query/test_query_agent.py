"""Agent loop, citations, history replay, generation tasks and titles against a fake Anthropic
client (scripted raw stream events through the SDK's own stream helper) and a fake library."""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic._models import construct_type
from anthropic.lib.streaming import BetaAsyncMessageStreamManager
from anthropic.types.beta import BetaMessage, BetaRawMessageStreamEvent

from ingest.query import QueryConfig, prompts
from ingest.query.agent import (
    TOOLS,
    ChatBusy,
    Generations,
    LibraryNotReady,
    answer_parts,
    clean_title,
    compact_pages,
    replay_content,
)
from ingest.query.library import DocumentInfo, Passage
from ingest.query.store import ChatStore

MODEL = "claude-sonnet-5-5"
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


# Fake library


def passage(chunk_id: str, doc_id: str, text: str, pages: tuple[int, ...] = (1,), also_in: int = 0) -> Passage:
    return Passage(
        chunk_id=chunk_id,
        doc_id=doc_id,
        seq=int(chunk_id[-1]),
        text=text,
        pages=pages,
        regions=({"page": pages[0], "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0},),
        section=(),
        filename=f"{doc_id}.pdf",
        job_number="2292",
        project="SZEKERES",
        score=0.9,
        also_in=also_in,
    )


DOCS = {
    "docA": DocumentInfo("docA", ("proposals/docA.pdf",), "docA.pdf", "2292", "SZEKERES", "2023", 2, False, False),
    "docB": DocumentInfo("docB", ("proposals/docB.pdf",), "docB.pdf", "2301", "ELLIOT", None, 1, False, False),
}
PASSAGES = {
    "a1": passage("a1", "docA", "Fee: $1,750.00", (1,)),
    "a2": passage("a2", "docA", "Fee: $1,835.00", (2, 3), also_in=2),
    "b1": passage("b1", "docB", "Retainer terms", (1,)),
}


class FakeLibrary:
    def __init__(self) -> None:
        self.ready = True
        self.not_ready = False
        self.searches: list[tuple[str, str | None, int]] = []
        self.block = threading.Event()
        self.block.set()
        self.blocked = threading.Event()

    def reload_if_stale(self) -> bool:
        if self.not_ready:
            raise LookupError("no current version")
        return False

    def contains(self, doc_id: str) -> bool:
        return doc_id in DOCS

    def document(self, doc_id: str) -> DocumentInfo | None:
        return DOCS.get(doc_id)

    def passage(self, chunk_id: str) -> Passage | None:
        return PASSAGES.get(chunk_id)

    def search(self, query: str, *, doc_id: str | None = None, k: int = 8) -> list[Passage]:
        self.searches.append((query, doc_id, k))
        if not self.block.is_set():
            self.blocked.set()
            self.block.wait(5)
        if query == "nothing":
            return []
        hits = [p for p in PASSAGES.values() if doc_id is None or p.doc_id == doc_id]
        return [p for p in hits if query in p.text or query == "*"][:k] or hits[:1]

    def read(self, doc_id: str) -> list[Passage]:
        return [p for p in PASSAGES.values() if p.doc_id == doc_id]

    def list_documents(self, **filters: str) -> list[DocumentInfo]:
        return [d for d in DOCS.values() if filters.get("job_number") in (None, d.job_number)]


# Fake client: scripted raw events through the SDK's BetaAsyncMessageStream


def ev(value: dict[str, Any]) -> Any:
    return construct_type(type_=BetaRawMessageStreamEvent, value=value)


PAUSE = "pause"


def text(index: int, chunks: list[str] | str, *, cite: list[str] = (), stop: bool = True) -> list[Any]:
    chunks = [chunks] if isinstance(chunks, str) else chunks
    out = [ev({"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})]
    out += [
        ev({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": t}}) for t in chunks
    ]
    for source in cite:
        citation = {
            "type": "search_result_location",
            "source": source,
            "title": f"{source} title",
            "cited_text": PASSAGES[source].text if source in PASSAGES else source,
            "search_result_index": 0,
            "start_block_index": 0,
            "end_block_index": 1,
        }
        out.append(
            ev(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "citations_delta", "citation": citation},
                }
            )
        )
    if stop:
        out.append(ev({"type": "content_block_stop", "index": index}))
    return out


def thinking(index: int, *, signed: bool = True) -> list[Any]:
    out = [
        ev(
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            }
        ),
        ev({"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": ""}}),
    ]
    if signed:
        out.append(
            ev(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "signature_delta", "signature": f"sig{index}"},
                }
            )
        )
        out.append(ev({"type": "content_block_stop", "index": index}))
    return out


def tool_use(index: int, name: str, args: dict[str, Any], *, stop: bool = True) -> list[Any]:
    start = {"type": "tool_use", "id": f"tu_{name}_{index}", "name": name, "input": {}}
    raw = json.dumps(args)
    out = [
        ev({"type": "content_block_start", "index": index, "content_block": start}),
        ev(
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": raw if stop else raw[:5]},
            }
        ),
    ]
    if stop:
        out.append(ev({"type": "content_block_stop", "index": index}))
    return out


def fallback(index: int, to: str) -> list[Any]:
    block = {
        "type": "fallback",
        "from": {"model": MODEL},
        "to": {"model": to},
        "trigger": {"type": "refusal", "category": "cyber"},
    }
    return [
        ev({"type": "content_block_start", "index": index, "content_block": block}),
        ev({"type": "content_block_stop", "index": index}),
    ]


def response(*blocks: list[Any], stop_reason: str | None = "end_turn", end: bool = True) -> list[Any]:
    usage = {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10}
    message = {"id": "msg", "type": "message", "role": "assistant", "model": MODEL, "content": [], "usage": usage}
    out = [ev({"type": "message_start", "message": message})]
    for block in blocks:
        out += block
    if end:
        out.append(ev({"type": "message_delta", "delta": {"stop_reason": stop_reason}, "usage": {"output_tokens": 20}}))
        out.append(ev({"type": "message_stop"}))
    return out


def answer(*sources_text: tuple[str, list[str]]) -> list[Any]:
    return response(*[text(i, t, cite=c) for i, (t, c) in enumerate(sources_text)])


def search(query: str, *, tool: str = "search_documents") -> list[Any]:
    args = {"query": query} if tool == "search_documents" else {}
    return response(thinking(0), tool_use(1, tool, args), stop_reason="tool_use")


class FakeRawStream:
    def __init__(self, events: list[Any], client: FakeClient, request_id: str) -> None:
        self.events = events
        self.client = client
        self.response = httpx2.Response(200, headers={"request-id": request_id}, request=REQUEST)

    async def __aiter__(self) -> Any:
        for event in self.events:
            if event == PAUSE:
                self.client.paused.set()
                await self.client.resume.wait()
            elif isinstance(event, Exception):
                raise event
            else:
                yield event

    async def close(self) -> None:
        pass


class FakeMessages:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def stream(self, **params: Any) -> BetaAsyncMessageStreamManager[Any]:
        self.client.calls.append(json.loads(json.dumps(params)))
        script = self.client.scripts.pop(0)

        async def request() -> FakeRawStream:
            if isinstance(script, Exception):
                raise script
            return FakeRawStream(script, self.client, f"req_{len(self.client.calls)}")

        return BetaAsyncMessageStreamManager(request())

    async def create(self, **params: Any) -> Any:
        self.client.title_calls.append(params)
        message = construct_type(
            type_=BetaMessage,
            value={
                "id": "msg_t",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [{"type": "text", "text": self.client.title}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        )
        message._request_id = "req_title"
        return message


class FakeClient:
    def __init__(self) -> None:
        self.scripts: list[Any] = []
        self.calls: list[dict[str, Any]] = []
        self.title_calls: list[dict[str, Any]] = []
        self.title = '"Szekeres fees."'
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()
        self.beta = type("Beta", (), {"messages": FakeMessages(self)})()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class Env:
    def __init__(self, tmp_path: Path, qconfig: QueryConfig) -> None:
        self.store = ChatStore(tmp_path / "chat.sqlite")
        self.library = FakeLibrary()
        self.client = FakeClient()
        self.log_dir = tmp_path / "logs"
        self.gens = Generations(
            self.store, self.library, qconfig, log_dir=self.log_dir, client_factory=lambda: self.client
        )  # type: ignore[arg-type]
        self.chat = self.store.create_chat().id

    async def ask(self, question: str, *scripts: Any, scope: str | None = None) -> list[Any]:
        self.client.scripts += scripts
        await self.gens.ask(self.chat, question, scope=scope)
        return await self.events()

    async def events(self) -> list[Any]:
        sub = self.gens.subscribe(self.chat)
        assert sub is not None
        return [e async for e in sub]

    def answers(self) -> list[Any]:
        return [m for m in self.store.messages(self.chat) if m.role == "assistant"]

    def log_lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in (self.log_dir / "chat.jsonl").read_text().splitlines()]


@pytest.fixture
def env(tmp_path: Path, qconfig: QueryConfig) -> Env:
    e = Env(tmp_path, qconfig)
    yield e
    e.library.block.set()
    e.store.close()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def assert_valid_history(messages: list[dict[str, Any]]) -> None:
    for i, m in enumerate(messages):
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        if m["role"] == "system":
            assert nxt is None or nxt["role"] == "assistant", f"system message at {i} followed by {nxt}"
        if m["role"] == "assistant":
            uses = [b["id"] for b in m["content"] if b["type"] == "tool_use"]
            assert m["content"], "empty assistant message"
            if uses:
                assert nxt is not None and nxt["role"] == "user"
                assert [b["tool_use_id"] for b in nxt["content"] if b["type"] == "tool_result"] == uses
            for b in m["content"]:
                if b["type"] == "thinking":
                    assert b["signature"]
                if b["type"] == "text":
                    assert b["text"]


# The loop


def test_search_then_cited_answer(env: Env) -> None:
    async def body() -> None:
        events = await env.ask(
            "What fees are in the Szekeres proposal?",
            search("Fee"),
            answer(("The fees are ", []), ("$1,750.00", ["a1"]), (" and ", []), ("$1,835.00", ["a2", "a2"]), (".", [])),
        )
        kinds = [e.event for e in events]
        assert kinds == [
            "search_started", "search_finished",
            "text_delta", "text_delta", "citation", "text_delta", "text_delta", "citation", "text_delta",
            "title", "done",
        ]  # fmt: skip
        assert [e.id for e in events] == list(range(1, len(events) + 1))
        started, finished = events[0].data, events[1].data
        assert started == {"round": 1, "tool": "search_documents", "query": "Fee", "scope": None}
        assert [p["chunk_id"] for p in finished["passages"]] == ["a1", "a2"] and finished["error"] is None
        c1, c2 = (e.data for e in events if e.event == "citation")
        assert (c1["n"], c1["chunk_id"], c1["doc_id"], c1["pages"], c1["filename"]) == (
            1,
            "a1",
            "docA",
            [1],
            "docA.pdf",
        )
        assert c1["regions"][0]["page"] == 1 and c1["cited_text"] == "Fee: $1,750.00"
        assert (c2["n"], c2["chunk_id"], c2["pages"]) == (2, "a2", [2, 3])
        assert events[-2].data == {"title": "Szekeres fees"}
        done = events[-1].data
        assert done["state"] == "complete"
        assert done["usage"] == {
            "input_tokens": 200,
            "output_tokens": 40,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
        }

        # The tool result sent back: search_result blocks with citations enabled.
        second = env.client.calls[1]["messages"]
        assert second[:2] == env.client.calls[0]["messages"]
        assert second[1] == {"role": "system", "content": prompts.scope_message(None)}
        tool_turn = second[3]
        assert tool_turn["role"] == "user"
        result = tool_turn["content"][0]
        assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_search_documents_1"
        assert result["content"][0] == {
            "type": "search_result",
            "source": "a1",
            "title": "docA.pdf · p. 1",
            "content": [{"type": "text", "text": "Fee: $1,750.00"}],
            "citations": {"enabled": True},
        }
        assert result["content"][1]["title"] == "docA.pdf · p. 2–3 · also in 2 other documents"
        assert second[2]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig0"}
        assert second[2]["content"][1] == {
            "type": "tool_use",
            "id": "tu_search_documents_1",
            "name": "search_documents",
            "input": {"query": "Fee"},
        }

        (row,) = env.answers()
        assert row.state == "complete" and row.text == "The fees are $1,750.00 and $1,835.00."
        assert row.request_ids == ["req_1", "req_2"] and row.model == MODEL
        assert row.ttft_ms is not None and row.total_ms is not None
        assert [m["role"] for m in row.content] == ["assistant", "user", "assistant"]
        citations = env.store.citations(env.chat)[row.id]
        assert [(c.n, c.source, c.chunk_id) for c in citations] == [(1, "a1", "a1"), (2, "a2", "a2")]
        assert answer_parts(row, citations) == [
            {"type": "text", "text": "The fees are $1,750.00"},
            {"type": "citation", "n": 1},
            {"type": "text", "text": " and $1,835.00"},
            {"type": "citation", "n": 2},
            {"type": "text", "text": "."},
        ]
        (step,) = env.store.search_steps(env.chat)[row.id]
        assert (step.round, step.tool, step.query, step.scope, step.error) == (1, "search_documents", "Fee", None, None)
        assert env.store.get_chat(env.chat).title == "Szekeres fees"

        lines = env.log_lines()
        assert {"call", "tool", "answer", "title"} <= {line["event"] for line in lines}
        assert all(line["chat_id"] == env.chat and line["message_id"] == row.id and "ts" in line for line in lines)
        (summary,) = [line for line in lines if line["event"] == "answer"]
        assert summary["queries"] == ["Fee"] and summary["rounds"] == 1 and summary["stop_reason"] == "end_turn"
        assert summary["usage"]["cache_read_input_tokens"] == 100 and summary["request_ids"] == ["req_1", "req_2"]
        (tool,) = [line for line in lines if line["event"] == "tool"]
        assert tool["results"] == [["a1", 0.9], ["a2", 0.9]]

    run(body())


def test_final_call_after_max_rounds_has_tool_choice_none(env: Env) -> None:
    async def body() -> None:
        await env.ask("q", search("*"), search("*"), search("*"), answer(("Done.", [])))
        calls = env.client.calls
        assert len(calls) == 4
        assert [c.get("tool_choice") for c in calls] == [None, None, None, {"type": "none"}]
        assert [s.round for s in env.store.search_steps(env.chat)[env.answers()[0].id]] == [1, 2, 3]

    run(body())


def test_request_params_identical_across_calls(env: Env) -> None:
    async def body() -> None:
        await env.ask("q1", search("Fee"), answer(("a", [])))
        await env.ask("q2", search("*"), search("*"), search("*"), answer(("b", [])), scope="docA")
        assert len(env.client.calls) == 6
        fixed = [{k: v for k, v in c.items() if k not in ("messages", "tool_choice")} for c in env.client.calls]
        assert all(f == fixed[0] for f in fixed)
        assert fixed[0] == {
            "model": MODEL,
            "max_tokens": 64000,
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "medium"},
            "cache_control": {"type": "ephemeral"},
            "system": prompts.SYSTEM,
            "tools": json.loads(json.dumps(TOOLS)),
        }
        assert [t["name"] for t in TOOLS] == ["search_documents", "read_document", "list_documents"]
        assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in TOOLS)
        # History is append-only: each request extends the previous one.
        for prev, cur in itertools.pairwise(env.client.calls):
            assert cur["messages"][: len(prev["messages"])] == prev["messages"]

    run(body())


def test_tools_outside_their_scope_return_error_text(env: Env) -> None:
    async def body() -> None:
        events = await env.ask("summarize", search("", tool="read_document"), answer(("Sorry.", [])))
        finished = next(e.data for e in events if e.event == "search_finished")
        assert finished["tool"] == "read_document" and finished["query"] is None
        assert "only applies when one document is in scope" in finished["error"]
        result = env.client.calls[1]["messages"][3]["content"][0]
        assert result["is_error"] is True and result["content"][0]["type"] == "text"
        assert "read_document" in result["content"][0]["text"]

        await env.ask("list", search("", tool="list_documents"), answer(("Sorry.", [])), scope="docA")
        result = env.client.calls[3]["messages"][-1]["content"][0]
        assert result["is_error"] is True and "list_documents only applies" in result["content"][0]["text"]
        steps = env.store.search_steps(env.chat)[env.answers()[1].id]
        assert steps[0].error and steps[0].passages == [] and steps[0].scope == "docA"

    run(body())


def test_single_document_scope(env: Env) -> None:
    async def body() -> None:
        events = await env.ask(
            "fees?", search("Fee"), search("", tool="read_document"), answer(("ok", [])), scope="docA"
        )
        assert env.library.searches == [("Fee", "docA", 8)]
        read = [e.data for e in events if e.event == "search_finished"][1]
        assert [p["chunk_id"] for p in read["passages"]] == ["a1", "a2"] and read["error"] is None
        assert env.store.get_chat(env.chat).scope == "docA"
        assert env.client.calls[0]["messages"][1]["content"] == prompts.scope_message("docA.pdf")

    run(body())


def test_list_documents_and_cited_document(env: Env) -> None:
    async def body() -> None:
        events = await env.ask(
            "Which proposals are for job 2292?",
            response(tool_use(0, "list_documents", {"job_number": "2292"}), stop_reason="tool_use"),
            answer(("docA", ["docA"])),
        )
        finished = next(e.data for e in events if e.event == "search_finished")
        assert finished["query"] == "job_number: 2292" and [d["doc_id"] for d in finished["passages"]] == ["docA"]
        result = env.client.calls[1]["messages"][-1]["content"][0]["content"][0]
        assert result["source"] == "docA" and result["title"] == "docA.pdf"
        assert (
            result["content"][0]["text"]
            == "Job # 2292 · Project: SZEKERES · Folder: 2023 · 2 pages · proposals/docA.pdf"
        )
        citation = next(e.data for e in events if e.event == "citation")
        assert (citation["doc_id"], citation["chunk_id"], citation["pages"], citation["regions"]) == (
            "docA",
            None,
            [1],
            [],
        )

    run(body())


def test_no_results_returns_text_block(env: Env) -> None:
    async def body() -> None:
        await env.ask("q", search("nothing"), answer(("Not found.", [])))
        result = env.client.calls[1]["messages"][-1]["content"][0]
        assert result["content"] == [{"type": "text", "text": "No passages found."}] and "is_error" not in result

    run(body())


def test_citations_in_third_question_resolve_by_source(env: Env) -> None:
    async def body() -> None:
        await env.ask("q1", search("1,750"), answer(("one", ["a1"])))
        await env.ask("q2", search("Retainer"), answer(("two", ["b1"])))
        # Cites a passage from the first answer, one from the second, and one only in the library.
        events = await env.ask("q3", answer(("three", ["b1", "a1"]), ("four", ["a2", "b1"])))
        cited = [(e.data["n"], e.data["chunk_id"], e.data["doc_id"]) for e in events if e.event == "citation"]
        assert cited == [(1, "b1", "docB"), (2, "a1", "docA"), (3, "a2", "docA"), (1, "b1", "docB")]
        third = env.answers()[2]
        rows = env.store.citations(env.chat)[third.id]
        assert [(c.n, c.source, c.pages) for c in rows] == [(1, "b1", [1]), (2, "a1", [1]), (3, "a2", [2, 3])]
        assert answer_parts(third, rows) == [
            {"type": "text", "text": "three"},
            {"type": "citation", "n": 1},
            {"type": "citation", "n": 2},
            {"type": "text", "text": "four"},
            {"type": "citation", "n": 3},
            {"type": "citation", "n": 1},
        ]
        # The third request replays both earlier exchanges in full.
        messages = env.client.calls[-1]["messages"]
        assert [m["role"] for m in messages] == [
            "user", "system", "assistant", "user", "assistant",
            "user", "system", "assistant", "user", "assistant",
            "user", "system",
        ]  # fmt: skip
        assert messages[4]["content"][0]["citations"][0]["source"] == "a1"
        assert_valid_history(messages)

    run(body())


# Stop, retry, history


def test_stop_mid_stream_tool_round_then_ask_again(env: Env) -> None:
    async def body() -> None:
        env.client.scripts.append(
            response(
                thinking(0),
                text(1, "Let me look."),
                thinking(2, signed=False),
                tool_use(3, "search_documents", {"query": "x"}, stop=False),
                end=False,
            )
            + [PAUSE]
        )
        await env.gens.ask(env.chat, "q1", scope=None)
        sub = env.gens.subscribe(env.chat)
        await env.client.paused.wait()
        await env.gens.stop(env.chat)
        events = [e async for e in sub]
        assert events[-1].event == "done" and events[-1].data["state"] == "stopped"
        assert not env.gens.generating(env.chat)
        (row,) = env.answers()
        assert row.state == "stopped" and row.text == "Let me look."
        assert answer_parts(row, []) == [{"type": "text", "text": "Let me look."}]
        assert replay_content(row.content) == []

        await env.ask("q2", answer(("Fine.", [])))
        messages = env.client.calls[-1]["messages"]
        assert [m["role"] for m in messages] == ["user", "user", "system"]
        assert_valid_history(messages)
        assert env.store.get_chat(env.chat).title == "Szekeres fees"

    run(body())


def test_stop_while_tool_runs_then_ask_again(env: Env) -> None:
    async def body() -> None:
        env.library.block.clear()
        env.client.scripts.append(search("Fee"))
        await env.gens.ask(env.chat, "q1", scope=None)
        await asyncio.to_thread(env.library.blocked.wait, 5)
        await env.gens.stop(env.chat)
        env.library.block.set()
        (row,) = env.answers()
        assert row.state == "stopped" and [m["role"] for m in row.content] == ["assistant"]

        await env.ask("q2", answer(("Fine.", [])))
        messages = env.client.calls[-1]["messages"]
        assert [m["role"] for m in messages] == ["user", "user", "system"]
        assert_valid_history(messages)

    run(body())


def test_stop_after_tool_results_keeps_completed_round(env: Env) -> None:
    async def body() -> None:
        env.client.scripts += [
            search("Fee"),
            response(thinking(0), text(1, "The fee", stop=False), end=False) + [PAUSE],
        ]
        await env.gens.ask(env.chat, "q1", scope=None)
        await env.client.paused.wait()
        await env.gens.stop(env.chat)
        (row,) = env.answers()
        assert row.state == "stopped" and row.text == "The fee"
        replayed = replay_content(row.content)
        assert [m["role"] for m in replayed] == ["assistant", "user", "assistant"]
        assert replayed[2]["content"] == [
            {"type": "thinking", "thinking": "", "signature": "sig0"},
            {"type": "text", "text": "The fee"},
        ]

        await env.ask("q2", answer(("Fine.", [])))
        messages = env.client.calls[-1]["messages"]
        assert [m["role"] for m in messages] == ["user", "system", "assistant", "user", "assistant", "user", "system"]
        assert_valid_history(messages)

    run(body())


def test_retry_replays_history_without_latest_answer(env: Env) -> None:
    async def body() -> None:
        await env.ask("q1", answer(("first", [])))
        await env.ask("q2", search("Fee"), answer(("second", ["a1"])))
        old = env.answers()[1]
        env.client.scripts.append(answer(("again", [])))
        new = await env.gens.retry(env.chat)
        assert new.id != old.id and new.scope is None
        await env.events()
        messages = env.client.calls[-1]["messages"]
        assert [m["role"] for m in messages] == ["user", "system", "assistant", "user", "system"]
        assert messages[3]["content"][0]["text"] == "q2"
        answers = env.answers()
        assert [a.text for a in answers] == ["first", "again"]
        assert old.id not in env.store.citations(env.chat) and old.id not in env.store.search_steps(env.chat)
        assert len(env.client.title_calls) == 1

    run(body())


def test_retry_uses_the_question_scope(env: Env) -> None:
    async def body() -> None:
        await env.ask("q1", search("Fee"), answer(("first", [])), scope="docA")
        env.store.update_chat(env.chat, scope=None)
        env.client.scripts += [search("Fee"), answer(("again", []))]
        new = await env.gens.retry(env.chat)
        await env.events()
        assert new.scope == "docA" and env.library.searches[-1] == ("Fee", "docA", 8)

    run(body())


def test_retry_without_answer_raises(env: Env) -> None:
    async def body() -> None:
        with pytest.raises(LookupError):
            await env.gens.retry(env.chat)

    run(body())


# Generation tasks


def test_generation_survives_disconnect_and_replays_to_new_subscriber(env: Env) -> None:
    async def body() -> None:
        env.client.scripts += [search("Fee"), response(text(0, ["Part one. "]), *[[PAUSE]], text(1, ["Part two."]))]
        await env.gens.ask(env.chat, "q", scope=None)
        first = env.gens.subscribe(env.chat)
        got = [await anext(first), await anext(first)]
        assert [e.event for e in got] == ["search_started", "search_finished"]
        await first.aclose()
        await env.client.paused.wait()
        assert env.gens.generating(env.chat)
        second = env.gens.subscribe(env.chat)
        env.client.resume.set()
        events = [e async for e in second]
        assert [e.id for e in events] == list(range(1, len(events) + 1))
        assert events[0].event == "search_started" and events[-1].event == "done"
        assert "".join(e.data["text"] for e in events if e.event == "text_delta") == "Part one. Part two."
        assert not env.gens.generating(env.chat) and env.gens.subscribe(env.chat) is None
        assert env.answers()[0].state == "complete"

    run(body())


def test_close_stores_answers_as_stopped(env: Env) -> None:
    async def body() -> None:
        env.client.scripts.append(response(text(0, "partial", stop=False), end=False) + [PAUSE])
        await env.gens.ask(env.chat, "q", scope=None)
        sub = env.gens.subscribe(env.chat)
        await env.client.paused.wait()
        await env.gens.close()
        events = [e async for e in sub]
        assert events[-1].data["state"] == "stopped"
        assert env.answers()[0].state == "stopped" and env.answers()[0].text == "partial"
        assert env.client.closed and not env.client.title_calls

    run(body())


def test_stop_immediately_after_ask(env: Env) -> None:
    async def body() -> None:
        env.client.scripts.append(answer(("x", [])))
        await env.gens.ask(env.chat, "q", scope=None)
        await env.gens.stop(env.chat)
        assert env.answers()[0].state in ("stopped", "complete") and not env.gens.generating(env.chat)

    run(body())


def test_ask_validation(env: Env) -> None:
    async def body() -> None:
        with pytest.raises(KeyError):
            await env.gens.ask("missing", "q", scope=None)
        with pytest.raises(ValueError):
            await env.gens.ask(env.chat, "q", scope="unknown")
        upload = env.store.add_upload(doc_id="docA", filename="docA.pdf", status="processing", removable=True)
        with pytest.raises(ValueError):
            await env.gens.ask(env.chat, "q", scope=None, upload_ids=[upload.id])
        env.library.not_ready = True
        with pytest.raises(LibraryNotReady):
            await env.gens.ask(env.chat, "q", scope=None)
        env.library.not_ready = False
        assert env.store.messages(env.chat) == []

        ready = env.store.add_upload(doc_id="docA", filename="docA.pdf", status="ready", removable=True)
        env.client.scripts.append(response(text(0, "x", stop=False), end=False) + [PAUSE])
        question, answer_row = await env.gens.ask(env.chat, "q", scope="docA", upload_ids=[ready.id])
        assert (question.role, question.state, question.scope) == ("user", "complete", "docA")
        assert (answer_row.role, answer_row.state, answer_row.model) == ("assistant", "generating", MODEL)
        assert [u.id for u in env.store.attachments(env.chat)[question.id]] == [ready.id]
        with pytest.raises(ChatBusy):
            await env.gens.ask(env.chat, "q2", scope=None)
        with pytest.raises(ChatBusy):
            await env.gens.retry(env.chat)
        await env.gens.close()

    run(body())


# Errors, refusals, fallback


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    resp = httpx2.Response(status, headers={"request-id": "req_err"}, request=REQUEST)
    return cls("failed", response=resp, body=None)


@pytest.mark.parametrize(
    ("script", "kind"),
    [
        (lambda: anthropic.APIConnectionError(request=REQUEST), "connection"),
        (lambda: anthropic.APITimeoutError(request=REQUEST), "connection"),
        (lambda: _status_error(anthropic.RateLimitError, 429), "rate_limit"),
        (lambda: _status_error(anthropic.InternalServerError, 500), "api"),
        (lambda: _status_error(anthropic.BadRequestError, 400), "api"),
        (lambda: response(text(0, "I can't help with that."), stop_reason="refusal"), "refusal"),
    ],
)
def test_error_kinds(env: Env, script: Any, kind: str) -> None:
    async def body() -> None:
        events = await env.ask("q", script())
        assert events[-1].event == "error" and events[-1].data["kind"] == kind
        row = env.answers()[0]
        assert row.state == "error" and row.error == kind
        if kind in ("rate_limit", "api"):
            assert row.request_ids == ["req_err"]
        if kind == "refusal":
            assert row.content == []
        assert not env.client.title_calls and env.store.get_chat(env.chat).title is None
        await env.ask("q2", answer(("ok", [])))
        assert_valid_history(env.client.calls[-1]["messages"])

    run(body())


def test_error_mid_stream_keeps_partial_text(env: Env) -> None:
    async def body() -> None:
        events = await env.ask(
            "q", response(text(0, "partial", stop=False), end=False) + [anthropic.APIConnectionError(request=REQUEST)]
        )
        assert events[-1].data["kind"] == "connection"
        row = env.answers()[0]
        assert row.text == "partial" and row.request_ids == ["req_1"]

    run(body())


def test_fallback_block_handling(env: Env) -> None:
    async def body() -> None:
        events = await env.ask(
            "q",
            response(
                thinking(0),
                text(1, "Partial "),
                tool_use(2, "search_documents", {"query": "x"}),
                fallback(3, "claude-opus-5-5"),
                thinking(4),
                text(5, "answer."),
            ),
        )
        assert events[-1].data["state"] == "complete"
        row = env.answers()[0]
        assert row.model == "claude-opus-5-5"
        assert row.content == [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Partial "},
                    {"type": "thinking", "thinking": "", "signature": "sig4"},
                    {"type": "text", "text": "answer."},
                ],
            }
        ]
        assert row.text == "Partial answer."
        assert len(env.client.calls) == 1

    run(body())


# Titles


def test_title_generated_once(env: Env) -> None:
    async def body() -> None:
        await env.ask("What fees are in the Szekeres proposal?", answer(("a", [])))
        await env.ask("q2", answer(("b", [])))
        assert len(env.client.title_calls) == 1
        params = env.client.title_calls[0]
        assert params["model"] == MODEL and params["max_tokens"] == 1000
        assert params["output_config"] == {"effort": "low"} and "tools" not in params
        assert params["messages"] == [
            {"role": "user", "content": prompts.title_prompt("What fees are in the Szekeres proposal?")}
        ]
        assert env.store.get_chat(env.chat).title == "Szekeres fees"

    run(body())


def test_title_failure_is_ignored(env: Env) -> None:
    async def body() -> None:
        async def boom(**params: Any) -> Any:
            raise anthropic.APIConnectionError(request=REQUEST)

        env.client.beta.messages.create = boom
        events = await env.ask("q", answer(("a", [])))
        assert [e.event for e in events][-1] == "done" and "title" not in [e.event for e in events]
        assert env.store.get_chat(env.chat).title is None
        assert any(line["event"] == "title_failed" for line in env.log_lines())

    run(body())


# Helpers


def test_clean_title() -> None:
    assert clean_title('"Szekeres fees."\n') == "Szekeres fees"
    assert clean_title("“Retainer terms”") == "Retainer terms"
    long = clean_title("word " * 30)
    assert len(long) <= 60 and not long.endswith(" ")


def test_compact_pages() -> None:
    assert compact_pages([3]) == "3"
    assert compact_pages([3, 4]) == "3–4"
    assert compact_pages([1, 3]) == "1, 3"
    assert compact_pages([5, 1, 2, 3]) == "1–3, 5"


def test_replay_content_is_noop_for_complete_answers() -> None:
    content = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "tool_use", "id": "t", "name": "read_document", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t", "content": [{"type": "text", "text": "x"}]}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "y"}]},
    ]
    assert replay_content(content) == content
