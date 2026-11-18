"""XLSX/XLS processor: every sheet becomes a PageResult with headers, rows and rendered text."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

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

MAX_SHEETS = 200


def _jsonable(v: Any) -> Any:
    """Convert cell values to JSON-safe types without losing precision (Decimal/float -> str-safe)."""
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return str(v)


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _is_label(c: Any) -> bool:
    """A string that is not just a number, e.g. 'Qty' yes, '1,200.50' no."""
    return isinstance(c, str) and not c.strip().replace(".", "", 1).replace(",", "").isdigit()


def detect_header_row(grid: list[list[Any]]) -> int | None:
    """Index of the first row that looks like a header: mostly non-empty text cells, followed by data.

    Scans the first 20 rows so title banners above the table are skipped. Returns None if unsure.
    """
    for i, row in enumerate(grid[:20]):
        filled = [c for c in row if not _blank(c)]
        if len(filled) < 2:
            continue
        text_cells = sum(_is_label(c) for c in filled)
        if text_cells / len(filled) >= 0.8 and i + 1 < len(grid) and any(not _blank(c) for c in grid[i + 1]):
            return i
    return None


def build_sheet_result(number: int, name: str, grid: list[list[Any]], merged_filled: int) -> PageResult:
    grid = [[_jsonable(c) for c in row] for row in grid]
    while grid and all(_blank(c) for c in grid[-1]):
        grid.pop()
    if not grid:
        return PageResult(number, PageKind.sheet, PageStatus.empty, label=name, source="table")

    width = max(len(r) for r in grid)
    grid = [r + [None] * (width - len(r)) for r in grid]
    header_idx = detect_header_row(grid)
    if header_idx is None:
        headers: list[str] = []
        first_body = 0
    else:
        headers = [str(c).strip() if not _blank(c) else f"column_{j + 1}" for j, c in enumerate(grid[header_idx])]
        first_body = header_idx + 1
    numbered = [(first_body + k + 1, r) for k, r in enumerate(grid[first_body:]) if not all(_blank(c) for c in r)]
    row_numbers = [n for n, _ in numbered]  # 1-based sheet row of each kept row (for cell-level references)
    body = [r for _, r in numbered]

    rendered = ["\t".join("" if c is None else str(c) for c in r) for r in body]
    lines = (["\t".join(headers)] if headers else []) + rendered
    table = {
        "sheet": name,
        "header_row": None if header_idx is None else header_idx + 1,
        "headers": headers,
        "rows": body,
        "row_numbers": row_numbers,
    }
    return PageResult(
        number, PageKind.sheet, PageStatus.ok, text="\n".join(lines), source="table", label=name,
        tables=[table], metadata={"row_count": len(body), "column_count": width, "merged_cells_filled": merged_filled},
    )


class ExcelProcessor(DocumentProcessor):
    extensions = frozenset({".xlsx", ".xls"})

    def validate(self, path: Path) -> None:
        names = self._sheet_names(path)
        if not names:
            raise ValidationError("no_sheets", "Workbook has no sheets")
        if len(names) > MAX_SHEETS:
            raise ValidationError("too_many_sheets", f"Workbook has {len(names)} sheets")

    def inspect(self, path: Path) -> FileInspection:
        names = self._sheet_names(path)
        return FileInspection(ALLOWED[path.suffix.lower()][0], path.stat().st_size, len(names), {"sheets": names})

    def _sheet_names(self, path: Path) -> list[str]:
        try:
            if path.suffix.lower() == ".xlsx":
                import openpyxl

                wb = openpyxl.load_workbook(path, read_only=True)
                try:
                    return list(wb.sheetnames)
                finally:
                    wb.close()
            import xlrd

            book = xlrd.open_workbook(path, on_demand=True)
            try:
                return book.sheet_names()
            finally:
                book.release_resources()
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("corrupt_file", f"Cannot read workbook: {exc}") from exc

    def process(self, path: Path, page_start: int = 1, page_end: int | None = None) -> Iterator[PageResult]:
        if path.suffix.lower() == ".xlsx":
            yield from self._process_xlsx(path, page_start, page_end)
        else:
            yield from self._process_xls(path, page_start, page_end)

    def _process_xlsx(self, path: Path, start: int, end: int | None) -> Iterator[PageResult]:
        import openpyxl

        try:
            # Not read_only: merged-cell ranges are only exposed in normal mode. Trade-off: higher memory.
            wb = openpyxl.load_workbook(path, data_only=True)
        except Exception as exc:
            raise ProcessingError("corrupt_file", f"Cannot read workbook: {exc}") from exc
        try:
            for number, ws in enumerate(wb.worksheets, start=1):
                if number < start or (end is not None and number > end):
                    continue
                try:
                    grid = [list(r) for r in ws.iter_rows(values_only=True)]
                    filled = 0
                    for rng in ws.merged_cells.ranges:
                        top = ws.cell(rng.min_row, rng.min_col).value
                        for r in range(rng.min_row, rng.max_row + 1):
                            for c in range(rng.min_col, rng.max_col + 1):
                                in_grid = r - 1 < len(grid) and c - 1 < len(grid[r - 1])
                                if (r, c) != (rng.min_row, rng.min_col) and in_grid:
                                    grid[r - 1][c - 1] = top
                                    filled += 1
                    yield build_sheet_result(number, ws.title, grid, filled)
                except Exception as exc:
                    yield PageResult(number, PageKind.sheet, PageStatus.failed, label=ws.title, source="table",
                                     error=f"sheet_error: {exc}")
        finally:
            wb.close()

    def _process_xls(self, path: Path, start: int, end: int | None) -> Iterator[PageResult]:
        import xlrd

        try:
            book = xlrd.open_workbook(path, formatting_info=True)
        except Exception as exc:
            raise ProcessingError("corrupt_file", f"Cannot read workbook: {exc}") from exc
        try:
            for number in range(1, book.nsheets + 1):
                if number < start or (end is not None and number > end):
                    continue
                sh = book.sheet_by_index(number - 1)
                try:
                    grid = [[self._xls_cell(book, sh.cell(r, c)) for c in range(sh.ncols)] for r in range(sh.nrows)]
                    filled = 0
                    for rlo, rhi, clo, chi in sh.merged_cells:
                        top = grid[rlo][clo]
                        for r in range(rlo, rhi):
                            for c in range(clo, chi):
                                if (r, c) != (rlo, clo):
                                    grid[r][c] = top
                                    filled += 1
                    yield build_sheet_result(number, sh.name, grid, filled)
                except Exception as exc:
                    yield PageResult(number, PageKind.sheet, PageStatus.failed, label=sh.name, source="table",
                                     error=f"sheet_error: {exc}")
        finally:
            book.release_resources()

    @staticmethod
    def _xls_cell(book: Any, cell: Any) -> Any:
        import xlrd

        if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
            return None
        if cell.ctype == xlrd.XL_CELL_DATE:
            try:
                return xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
            except xlrd.xldate.XLDateError:
                return cell.value
        if cell.ctype == xlrd.XL_CELL_BOOLEAN:
            return bool(cell.value)
        if cell.ctype == xlrd.XL_CELL_ERROR:
            return f"#ERR{cell.value}"
        return cell.value
