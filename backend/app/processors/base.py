"""Common processor contract and structured output types.

Every processor returns one `PageResult` per page/sheet/image. Failures are recorded on the
result (never skipped silently) so callers can persist them and retry.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PageKind(str, enum.Enum):
    digital = "digital"
    scanned = "scanned"
    image = "image"
    sheet = "sheet"
    csv = "csv"


class PageStatus(str, enum.Enum):
    ok = "ok"
    failed = "failed"
    empty = "empty"  # readable but contained no text; distinct from failure


class ProcessingError(Exception):
    """Raised for unreadable/corrupt/rejected files. Carries a stable code for the job record."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ValidationError(ProcessingError):
    """File rejected before processing (type, size, signature, encryption...)."""


@dataclass(slots=True)
class PageResult:
    page_number: int  # 1-based; sheet index for spreadsheets
    kind: PageKind
    status: PageStatus
    text: str = ""
    source: str = "native"  # native | ocr | table
    ocr_confidence: float | None = None  # 0-100, only for OCR output
    label: str | None = None  # sheet name
    error: str | None = None
    tables: list[dict[str, Any]] = field(default_factory=list)  # structured rows for sheets/CSV
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FileInspection:
    mime_type: str
    size_bytes: int
    page_count: int | None
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentProcessor(ABC):
    """Interface: validate -> inspect -> process (streamed results)."""

    #: lowercase extensions (with dot) handled by this processor
    extensions: frozenset[str] = frozenset()

    @abstractmethod
    def validate(self, path: Path) -> None:
        """Content inspection. Raise ValidationError for unusable files."""

    @abstractmethod
    def inspect(self, path: Path) -> FileInspection:
        """Cheap metadata extraction (page count, sheets...) without full processing."""

    @abstractmethod
    def process(self, path: Path, page_start: int = 1, page_end: int | None = None) -> Iterator[PageResult]:
        """Yield results lazily so callers can persist incrementally and memory stays bounded."""
