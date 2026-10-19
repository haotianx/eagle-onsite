"""Parse a PDF with the Docling standard pipeline: text layer where present, OCR only for bitmaps."""

from __future__ import annotations

import time
from importlib.metadata import version
from typing import Any

import pypdfium2 as pdfium
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import (
    OcrAutoOptions,
    OcrMacOptions,
    OcrOptions,
    PdfPipelineOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DoclingDocument, TableItem

from ingest.contract import Doc, NodeContext

ARTIFACT_TYPE = "parse"

MIN_TEXT_LAYER_CHARS = 20
"""A page whose text layer has fewer non-whitespace characters counts as lacking one (ocr)."""


def _ocr_options(engine: str) -> OcrOptions | None:
    if engine == "ocrmac":
        return OcrMacOptions()
    if engine == "auto":
        return OcrAutoOptions()
    if engine == "none":
        return None
    raise ValueError(f"unknown ocr_engine {engine!r} (expected ocrmac, auto or none)")


def describe(params: dict) -> dict:
    return {
        "engine": "docling",
        "pipeline": "standard",
        "docling": version("docling"),
        "docling_core": version("docling-core"),
        "docling_ibm_models": version("docling-ibm-models"),
        "ocr_engine": params.get("ocr_engine", "ocrmac"),
        "ocrmac": version("ocrmac"),
    }


def setup(params: dict) -> DocumentConverter:
    ocr = _ocr_options(params.get("ocr_engine", "ocrmac"))
    options = PdfPipelineOptions(do_ocr=ocr is not None, do_table_structure=True)
    if ocr is not None:
        options.ocr_options = ocr
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    converter.initialize_pipeline(InputFormat.PDF)
    return converter


def _text_layer_chars(path: Any) -> dict[int, tuple[float, float, int]]:
    """page (1-based) -> (width, height, non-whitespace chars in the embedded text layer)."""
    pdf = pdfium.PdfDocument(path)
    try:
        pages = {}
        for i, page in enumerate(pdf, start=1):
            text = page.get_textpage().get_text_bounded()
            width, height = page.get_size()
            pages[i] = (float(width), float(height), sum(not c.isspace() for c in text))
        return pages
    finally:
        pdf.close()


def _docling_chars(document: DoclingDocument) -> dict[int, int]:
    """page (1-based) -> characters of parsed text (text items and table cells) on that page."""
    chars: dict[int, int] = {}
    for item, _ in document.iterate_items():
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        if isinstance(item, TableItem):
            n = sum(len(cell.text) for cell in item.data.table_cells)
        else:
            n = len(getattr(item, "text", "") or "")
        page = prov[0].page_no
        chars[page] = chars.get(page, 0) + n
    return chars


def run(doc: Doc, inputs: dict[str, Any], params: dict, ctx: NodeContext) -> dict[str, Any]:
    if doc.flags:
        raise ValueError(f"cannot parse {doc.paths[0]}: flagged {', '.join(doc.flags)}")
    converter: DocumentConverter = ctx.state

    start = time.perf_counter()
    result = converter.convert(doc.file, raises_on_error=False)
    if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        errors = "; ".join(e.error_message for e in result.errors) or result.status.value
        raise RuntimeError(f"docling conversion failed: {errors}")
    for error in result.errors:
        ctx.log.warning("docling: %s", error.error_message)
    document = result.document

    layer = _text_layer_chars(doc.file)
    parsed = _docling_chars(document)
    pages = []
    for n, (pdf_width, pdf_height, layer_chars) in sorted(layer.items()):
        size = document.pages[n].size if n in document.pages else None
        pages.append(
            {
                "page": n,
                "width": float(size.width) if size else pdf_width,
                "height": float(size.height) if size else pdf_height,
                "text_chars": parsed.get(n, 0),
                "ocr": layer_chars < MIN_TEXT_LAYER_CHARS,
            }
        )

    elapsed = time.perf_counter() - start
    ctx.log.info(
        "parsed %d pages (%d without text layer) in %.1fs",
        len(pages),
        sum(p["ocr"] for p in pages),
        elapsed,
    )
    return {"docling": document.export_to_dict(), "pages": pages}
