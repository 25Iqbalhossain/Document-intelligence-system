"""PDF processor: per-page digital vs scanned detection, OCR fallback, bounded memory.

The document is opened per `process()` call (i.e. per page batch), only the requested pages
are touched, and each rendered page is released before the next one is rendered.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pymupdf

from app.processors.base import (
    DocumentProcessor,
    FileInspection,
    PageKind,
    PageResult,
    PageStatus,
    ProcessingError,
    ValidationError,
)
from app.processors.images.preprocess import orientation_correct, preprocess
from app.processors.ocr import OcrEngine, TesseractEngine

MIN_NATIVE_CHARS = 25  # below this a page is treated as scanned
MAX_RENDER_PIXELS = 40_000_000  # caps memory for huge page sizes


class PdfProcessor(DocumentProcessor):
    extensions = frozenset({".pdf"})

    def __init__(
        self,
        ocr: OcrEngine | None = None,
        ocr_dpi: int = 300,
        min_native_chars: int = MIN_NATIVE_CHARS,
        max_pages: int = 5000,
    ) -> None:
        self.ocr = ocr or TesseractEngine()
        self.ocr_dpi = ocr_dpi
        self.min_native_chars = min_native_chars
        self.max_pages = max_pages

    def _open(self, path: Path) -> pymupdf.Document:
        try:
            doc = pymupdf.open(path)
        except Exception as exc:  # pymupdf raises several error types for corrupt files
            raise ValidationError("corrupt_file", f"Cannot open PDF: {exc}") from exc
        if doc.needs_pass:
            doc.close()
            raise ValidationError("encrypted", "Password-protected PDFs are not supported")
        return doc

    def validate(self, path: Path) -> None:
        doc = self._open(path)
        try:
            if doc.page_count == 0:
                raise ValidationError("no_pages", "PDF has no pages")
            if doc.page_count > self.max_pages:
                raise ValidationError("too_many_pages", f"PDF has {doc.page_count} pages; limit {self.max_pages}")
        finally:
            doc.close()

    def inspect(self, path: Path) -> FileInspection:
        doc = self._open(path)
        try:
            meta = {k: v for k, v in (doc.metadata or {}).items() if v}
            return FileInspection("application/pdf", path.stat().st_size, doc.page_count, {"pdf_metadata": meta})
        finally:
            doc.close()

    def process(self, path: Path, page_start: int = 1, page_end: int | None = None) -> Iterator[PageResult]:
        doc = self._open(path)
        try:
            last = doc.page_count if page_end is None else min(page_end, doc.page_count)
            if page_start < 1 or page_start > last:
                raise ProcessingError("bad_page_range", f"Invalid page range {page_start}-{page_end}")
            for number in range(page_start, last + 1):
                yield self._process_page(doc, number)
        finally:
            doc.close()

    def _process_page(self, doc: pymupdf.Document, number: int) -> PageResult:
        try:
            page = doc[number - 1]
            native = page.get_text("text").strip()
            if len(native) >= self.min_native_chars:
                return PageResult(number, PageKind.digital, PageStatus.ok, text=native, source="native")
            return self._ocr_page(page, number, native)
        except ProcessingError as exc:
            return PageResult(number, PageKind.scanned, PageStatus.failed, error=f"{exc.code}: {exc.message}")
        except Exception as exc:  # one bad page must not kill the batch; failure is recorded, not hidden
            return PageResult(number, PageKind.scanned, PageStatus.failed, error=f"page_error: {exc}")

    def _ocr_page(self, page: pymupdf.Page, number: int, native: str) -> PageResult:
        scale = self.ocr_dpi / 72
        rect = page.rect
        pixels = rect.width * scale * rect.height * scale
        if pixels > MAX_RENDER_PIXELS:
            scale *= (MAX_RENDER_PIXELS / pixels) ** 0.5
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False)
        try:
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
        finally:
            del pix  # release the pixmap buffer before OCR
        img, rotated = orientation_correct(img)
        prepared, fixes = preprocess(img)
        result = self.ocr.recognize(prepared)
        meta = {"render_scale": round(scale, 3), "rotated_degrees": rotated, **fixes}
        if result.word_count == 0:
            # Rendered and read fine, but no text: a blank/graphic page. Not a failure.
            return PageResult(number, PageKind.scanned, PageStatus.empty, text=native, source="ocr", metadata=meta)
        return PageResult(
            number,
            PageKind.scanned,
            PageStatus.ok,
            text=result.text,
            source="ocr",
            ocr_confidence=result.confidence,
            metadata=meta,
        )
