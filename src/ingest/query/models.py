"""Request, response and SSE event models of the chat API (Query-Design §13)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ingest.api.models import Region

ToolName = Literal["search_documents", "read_document", "list_documents"]
ErrorKind = Literal["connection", "rate_limit", "api_error", "refusal"]


class LibraryStatus(BaseModel):
    ready: bool
    """False when there is no current embed version ("Library isn't ready")."""
    documents: int


class ChatSummary(BaseModel):
    id: int
    title: str | None
    updated: str
    generating: bool


class ChatPatch(BaseModel):
    """Rename, or set the scope chip (a doc id, or null for All documents). Only fields present
    in the body change."""

    title: str | None = None
    scope: str | None = None


class UploadOut(BaseModel):
    id: int
    doc_id: str | None
    filename: str
    status: Literal["processing", "ready", "failed"]
    error: str | None
    """Failed: not a PDF, over 50 MB, encrypted, unreadable, no pages, …"""
    removable: bool
    """Uploads can be removed from the library; documents from the proposals folder can't."""
    removed: bool
    progress: dict[str, float]
    """While processing: what is done so far (pages, chunks, vectors)."""


class PassageOut(BaseModel):
    chunk_id: str | None
    """None for list_documents results."""
    doc_id: str
    filename: str | None
    job_number: str | None = None
    project: str | None = None
    pages: list[int]
    regions: list[Region]
    score: float | None
    duplicates: int = 0
    """Other documents with the same passage (collapsed)."""


class SearchStepOut(BaseModel):
    round: int
    tool: ToolName
    query: str | None
    scope: str | None
    scope_name: str
    """"all documents" or the scoped document's filename."""
    passages: list[PassageOut]


class CitationOut(BaseModel):
    n: int
    doc_id: str
    chunk_id: str | None
    filename: str | None
    pages: list[int]
    regions: list[Region]
    cited_text: str | None


class AnswerPart(BaseModel):
    """One text block of an answer; `citations` are the numbers [n] that follow it."""

    text: str
    citations: list[int] = Field(default_factory=list)


class MessageOut(BaseModel):
    id: int
    seq: int
    role: Literal["user", "assistant"]
    state: Literal["generating", "complete", "stopped", "error"]
    error: ErrorKind | None
    scope: str | None
    scope_name: str
    created: str
    text: str
    """The question, or the answer's plain text (what Copy copies)."""
    parts: list[AnswerPart]
    """Answers only."""
    attachments: list[UploadOut]
    """Questions only: the document cards sent with it."""
    search_steps: list[SearchStepOut]
    citations: list[CitationOut]


class ChatDetail(ChatSummary):
    scope: str | None
    messages: list[MessageOut]


class AskIn(BaseModel):
    text: str
    scope: str | None = None
    upload_ids: list[int] = Field(default_factory=list)


class AskOut(BaseModel):
    question: MessageOut
    answer: MessageOut
    """The answer being generated (state "generating"); follow it on /chat/chats/{id}/events."""


# SSE event payloads. /chat/chats/{id}/events sends, in order and replayed from the start on
# resubscribe: search_started, search_finished, text_delta, citation (any number), title, then
# done or error. With no answer generating, it sends a single `idle` and ends.


class SearchStartedEvent(BaseModel):
    round: int
    tool: ToolName
    query: str | None
    scope: str | None
    scope_name: str


class SearchFinishedEvent(BaseModel):
    round: int
    passages: list[PassageOut]


class TextDeltaEvent(BaseModel):
    index: int
    """Index into the answer's parts."""
    text: str


class CitationEvent(BaseModel):
    index: int
    """The part the citation follows."""
    citation: CitationOut


class TitleEvent(BaseModel):
    title: str


class DoneEvent(BaseModel):
    message_id: int
    state: Literal["complete", "stopped"]
    usage: dict[str, Any]


class ErrorEvent(BaseModel):
    message_id: int
    kind: ErrorKind
    message: str


# /chat/uploads/{id}/events sends `progress` while processing, then `ready` or `failed` and ends
# (immediately when the upload is already ready or failed).


class UploadProgressEvent(BaseModel):
    node: str
    """The node that just finished for this document (e.g. ocr, chunk, embed)."""
    progress: dict[str, float]


class UploadDoneEvent(BaseModel):
    upload: UploadOut
