"""Image processor for JPG/JPEG/PNG/TIFF (multi-page TIFF supported, one page per frame)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from app.processors.base import (
    DocumentProcessor,
    FileInspection,
    PageKind,
    PageResult,
    PageStatus,
    ProcessingError,
    ValidationError,
)
from app.processors.file_checks import ALLOWED
from app.processors.images.preprocess import orientation_correct, preprocess
from app.processors.ocr import OcrEngine, TesseractEngine

MAX_PIXELS = 100_000_000  # decompression-bomb guard
MAX_FRAMES = 500


class ImageProcessor(DocumentProcessor):
    extensions = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff"})

    def __init__(self, ocr: OcrEngine | None = None) -> None:
        self.ocr = ocr or TesseractEngine()

    def _open(self, path: Path) -> Image.Image:
        try:
            img = Image.open(path)
            img.verify()  # cheap integrity check; image must be re-opened afterwards
            img = Image.open(path)
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValidationError("corrupt_file", f"Cannot read image: {exc}") from exc
        if img.width * img.height > MAX_PIXELS:
            raise ValidationError("image_too_large", f"Image is {img.width}x{img.height} pixels")
        return img

    def validate(self, path: Path) -> None:
        with self._open(path) as img:
            if getattr(img, "n_frames", 1) > MAX_FRAMES:
                raise ValidationError("too_many_pages", f"Image has {img.n_frames} frames")

    def inspect(self, path: Path) -> FileInspection:
        with self._open(path) as img:
            mime = ALLOWED[path.suffix.lower()][0]
            meta = {"width": img.width, "height": img.height, "mode": img.mode, "format": img.format}
            return FileInspection(mime, path.stat().st_size, getattr(img, "n_frames", 1), meta)

    def process(self, path: Path, page_start: int = 1, page_end: int | None = None) -> Iterator[PageResult]:
        with self._open(path) as img:
            total = getattr(img, "n_frames", 1)
            last = total if page_end is None else min(page_end, total)
            if page_start < 1 or page_start > last:
                raise ProcessingError("bad_page_range", f"Invalid page range {page_start}-{page_end}")
            for idx, frame in enumerate(ImageSequence.Iterator(img), start=1):
                if idx < page_start:
                    continue
                if idx > last:
                    break
                yield self._process_frame(frame, idx)

    def _process_frame(self, frame: Image.Image, number: int) -> PageResult:
        try:
            frame = ImageOps.exif_transpose(frame)  # honour EXIF orientation (phone photos)
            gray = np.array(frame.convert("L"))
            gray, rotated = orientation_correct(gray)
            prepared, fixes = preprocess(gray)
            result = self.ocr.recognize(prepared)
            meta = {"width": gray.shape[1], "height": gray.shape[0], "rotated_degrees": rotated, **fixes}
            if result.word_count == 0:
                return PageResult(number, PageKind.image, PageStatus.empty, source="ocr", metadata=meta)
            return PageResult(
                number, PageKind.image, PageStatus.ok, text=result.text, source="ocr",
                ocr_confidence=result.confidence, metadata=meta,
            )
        except ProcessingError as exc:
            return PageResult(number, PageKind.image, PageStatus.failed, error=f"{exc.code}: {exc.message}")
        except Exception as exc:
            return PageResult(number, PageKind.image, PageStatus.failed, error=f"frame_error: {exc}")
