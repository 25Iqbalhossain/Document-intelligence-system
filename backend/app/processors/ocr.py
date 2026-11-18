"""OCR engine abstraction (Tesseract default; PaddleOCR can be added behind the same interface)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from app.processors.base import ProcessingError


@dataclass(slots=True)
class OcrResult:
    text: str
    confidence: float | None  # mean word confidence, 0-100; None when no words were recognised
    word_count: int


class OcrEngine(ABC):
    @abstractmethod
    def recognize(self, image: np.ndarray) -> OcrResult:
        """Run OCR on a grayscale or BGR uint8 image."""


class TesseractEngine(OcrEngine):
    def __init__(self, languages: str = "eng", timeout_s: int = 120) -> None:
        self.languages = languages
        self.timeout_s = timeout_s

    def recognize(self, image: np.ndarray) -> OcrResult:
        import pytesseract

        try:
            data = pytesseract.image_to_data(
                image, lang=self.languages, output_type=pytesseract.Output.DICT, timeout=self.timeout_s
            )
        except pytesseract.TesseractError as exc:
            raise ProcessingError("ocr_failed", f"Tesseract error: {exc}") from exc
        except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
            raise ProcessingError("ocr_timeout", f"OCR exceeded {self.timeout_s}s") from exc

        words: list[str] = []
        confs: list[float] = []
        lines: dict[tuple[int, int, int], list[str]] = {}
        for txt, conf, blk, par, ln in zip(
            data["text"], data["conf"], data["block_num"], data["par_num"], data["line_num"], strict=True
        ):
            if not txt.strip():
                continue
            c = float(conf)
            if c >= 0:  # -1 marks non-word rows
                confs.append(c)
            words.append(txt)
            lines.setdefault((blk, par, ln), []).append(txt)
        text = "\n".join(" ".join(w) for w in lines.values())
        return OcrResult(text=text, confidence=(sum(confs) / len(confs)) if confs else None, word_count=len(words))
