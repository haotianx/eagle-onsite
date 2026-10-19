"""Root node for the query integration tests: each page's text layer."""

from __future__ import annotations

from typing import Any

import pypdfium2 as pdfium

from ingest.contract import Doc, NodeContext


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> dict[str, Any]:
    pdf = pdfium.PdfDocument(doc.file)
    try:
        return {"pages": [{"page": i + 1, "text": pdf[i].get_textpage().get_text_range()} for i in range(len(pdf))]}
    finally:
        pdf.close()
