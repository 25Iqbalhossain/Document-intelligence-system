"""CSV processor: encoding detection, delimiter sniffing, malformed-row capture."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from charset_normalizer import from_bytes

from app.processors.base import (
    DocumentProcessor,
    FileInspection,
    PageKind,
    PageResult,
    PageStatus,
    ProcessingError,
    ValidationError,
)

SAMPLE_BYTES = 256 * 1024
CANDIDATE_DELIMITERS = [",", ";", "\t", "|"]
MAX_MALFORMED_RECORDED = 1000


def detect_encoding(sample: bytes) -> str:
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as exc:
        # A sample cut mid-character is still valid UTF-8; only treat as failure if not at the tail.
        if exc.start >= len(sample) - 4:
            return "utf-8"
    best = from_bytes(sample).best()
    if best is None:
        raise ProcessingError("encoding_undetected", "Could not detect CSV text encoding")
    if _is_single_byte_codepage(best.encoding):
        # Statistical detection cannot tell single-byte code pages apart on short samples (it may
        # pick cp1250 for Latin-1 text). cp1252 is what Excel on Windows writes, so prefer it when valid.
        try:
            sample.decode("cp1252")
            return "cp1252"
        except UnicodeDecodeError:
            pass
    return best.encoding


def _is_single_byte_codepage(name: str) -> bool:
    n = name.lower().replace("-", "").replace("_", "")
    return n.startswith(("cp125", "iso8859", "latin", "mac", "windows125"))


def detect_delimiter(text_sample: str) -> str:
    try:
        return csv.Sniffer().sniff(text_sample, delimiters="".join(CANDIDATE_DELIMITERS)).delimiter
    except csv.Error:
        # Fallback: delimiter with the most consistent non-zero count across the first lines.
        lines = [ln for ln in text_sample.splitlines()[:20] if ln.strip()]
        best, best_score = ",", -1
        for d in CANDIDATE_DELIMITERS:
            counts = [ln.count(d) for ln in lines]
            if counts and min(counts) > 0 and len(set(counts)) == 1 and counts[0] > best_score:
                best, best_score = d, counts[0]
        return best


class CsvProcessor(DocumentProcessor):
    extensions = frozenset({".csv"})

    def _prepare(self, path: Path) -> tuple[str, str]:
        with path.open("rb") as f:
            sample = f.read(SAMPLE_BYTES)
        encoding = detect_encoding(sample)
        try:
            text = sample.decode(encoding, errors="replace")
        except LookupError as exc:
            raise ProcessingError("encoding_undetected", f"Unknown encoding {encoding}") from exc
        return encoding, detect_delimiter(text)

    def validate(self, path: Path) -> None:
        try:
            self._prepare(path)
        except ProcessingError as exc:
            raise ValidationError(exc.code, exc.message) from exc

    def inspect(self, path: Path) -> FileInspection:
        encoding, delimiter = self._prepare(path)
        return FileInspection("text/csv", path.stat().st_size, 1, {"encoding": encoding, "delimiter": delimiter})

    def process(self, path: Path, page_start: int = 1, page_end: int | None = None) -> Iterator[PageResult]:
        if page_start != 1:
            raise ProcessingError("bad_page_range", "CSV has exactly one page")
        encoding, delimiter = self._prepare(path)
        headers: list[str] = []
        rows: list[list[str]] = []
        row_numbers: list[int] = []
        malformed: list[dict] = []
        malformed_total = 0
        try:
            with path.open("r", encoding=encoding, errors="replace", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                for row in reader:
                    if not headers:
                        if any(c.strip() for c in row):
                            headers = [c.strip() or f"column_{i + 1}" for i, c in enumerate(row)]
                        continue
                    if not any(c.strip() for c in row):
                        continue
                    if len(row) != len(headers):
                        malformed_total += 1
                        if len(malformed) < MAX_MALFORMED_RECORDED:
                            malformed.append({"line": reader.line_num, "fields": len(row), "raw": row})
                        continue  # kept out of `rows` so bad data never masquerades as good data
                    rows.append(row)
                    row_numbers.append(reader.line_num)
        except csv.Error as exc:
            yield PageResult(1, PageKind.csv, PageStatus.failed, source="table", error=f"csv_error: {exc}")
            return

        if not headers:
            yield PageResult(1, PageKind.csv, PageStatus.empty, source="table")
            return
        text = "\n".join(["\t".join(headers)] + ["\t".join(r) for r in rows])
        yield PageResult(
            1, PageKind.csv, PageStatus.ok, text=text, source="table",
            tables=[{"sheet": None, "header_row": 1, "headers": headers, "rows": rows, "row_numbers": row_numbers}],
            metadata={
                "encoding": encoding,
                "delimiter": delimiter,
                "row_count": len(rows),
                "malformed_row_count": malformed_total,
                "malformed_rows": malformed,
                "duplicate_headers": sorted({h for h in headers if headers.count(h) > 1}),
            },
        )

