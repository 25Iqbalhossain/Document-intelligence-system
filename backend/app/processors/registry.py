"""Selects the processor for a file and runs the shared pre-checks."""

from __future__ import annotations

from pathlib import Path

from app.processors.base import DocumentProcessor, ValidationError
from app.processors.csv.processor import CsvProcessor
from app.processors.excel.processor import ExcelProcessor
from app.processors.file_checks import check_file
from app.processors.images.processor import ImageProcessor
from app.processors.ocr import OcrEngine
from app.processors.pdf.processor import PdfProcessor


def get_processor(path: Path, max_bytes: int, ocr: OcrEngine | None = None) -> DocumentProcessor:
    """Validate type/size/signature, then return a processor whose own content checks also pass."""
    ext, _ = check_file(path, max_bytes)
    processors: list[DocumentProcessor] = [
        PdfProcessor(ocr=ocr),
        ImageProcessor(ocr=ocr),
        ExcelProcessor(),
        CsvProcessor(),
    ]
    for proc in processors:
        if ext in proc.extensions:
            proc.validate(path)
            return proc
    raise ValidationError("unsupported_type", f"No processor for '{ext}'")
