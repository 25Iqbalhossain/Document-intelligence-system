import pytest

from app.processors.base import PageKind, PageStatus
from app.processors.csv.processor import CsvProcessor
from app.processors.excel.processor import ExcelProcessor
from tests.unit import factories as f


def test_xlsx_multiple_sheets_names_headers_and_banner(tmp_path):
    p = f.xlsx(tmp_path / "a.xlsx")
    proc = ExcelProcessor()
    proc.validate(p)
    assert proc.inspect(p).metadata["sheets"] == ["Invoices", "Lines", "Empty"]
    res = list(proc.process(p))
    assert [(r.page_number, r.label) for r in res] == [(1, "Invoices"), (2, "Lines"), (3, "Empty")]
    inv = res[0].tables[0]
    assert inv["headers"] == ["Invoice No", "Date", "Supplier", "Total"]
    assert inv["header_row"] == 3  # banner and blank row skipped
    assert inv["rows"][0] == ["INV-001", "2025-01-05", "Rahim Traders", "1150.5"] or inv["rows"][0][3] == 1150.5


def test_xlsx_zero_is_kept_not_blank(tmp_path):
    res = list(ExcelProcessor().process(f.xlsx(tmp_path / "a.xlsx")))
    assert res[0].tables[0]["rows"][1][3] == 0


def test_xlsx_merged_cells_filled_down(tmp_path):
    res = list(ExcelProcessor().process(f.xlsx(tmp_path / "a.xlsx")))
    rows = res[1].tables[0]["rows"]
    assert [r[0] for r in rows] == ["INV-001", "INV-002", "INV-002"]
    assert res[1].metadata["merged_cells_filled"] == 1


def test_empty_sheet_is_reported_not_skipped(tmp_path):
    res = list(ExcelProcessor().process(f.xlsx(tmp_path / "a.xlsx")))
    assert res[2].status == PageStatus.empty and res[2].label == "Empty"


def test_legacy_xls(tmp_path):
    p = f.xls(tmp_path / "old.xls")
    proc = ExcelProcessor()
    proc.validate(p)
    (r,) = proc.process(p)
    t = r.tables[0]
    assert r.label == "Legacy" and t["headers"] == ["Invoice No", "Total"]
    assert [row[0] for row in t["rows"]] == ["OLD-1", "OLD-2", "OLD-2"]


def test_sheet_range_selection(tmp_path):
    res = list(ExcelProcessor().process(f.xlsx(tmp_path / "a.xlsx"), 2, 2))
    assert [r.label for r in res] == ["Lines"]


def csv_file(tmp_path, content: bytes, name="a.csv"):
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_csv_basic_and_column_names_preserved(tmp_path):
    p = csv_file(tmp_path, b"Invoice No,Total\nINV-1,100\nINV-2,0\n")
    (r,) = CsvProcessor().process(p)
    t = r.tables[0]
    assert r.kind == PageKind.csv and t["headers"] == ["Invoice No", "Total"]
    assert t["rows"] == [["INV-1", "100"], ["INV-2", "0"]]


def test_csv_semicolon_delimiter(tmp_path):
    p = csv_file(tmp_path, b"a;b;c\n1;2;3\n4;5;6\n")
    (r,) = CsvProcessor().process(p)
    assert r.metadata["delimiter"] == ";" and r.tables[0]["rows"][1] == ["4", "5", "6"]


def test_csv_utf8_bom_and_bengali(tmp_path):
    content = "﻿সরবরাহকারী,মোট\nরহিম ট্রেডার্স,১১৫০\n".encode()
    (r,) = CsvProcessor().process(csv_file(tmp_path, content))
    assert r.metadata["encoding"] == "utf-8-sig"
    assert r.tables[0]["headers"] == ["সরবরাহকারী", "মোট"]
    assert r.tables[0]["rows"][0][1] == "১১৫০"


def test_csv_latin1_detected(tmp_path):
    content = "name,city\nJosé,São Paulo\nMüller,Köln\nZoë,Zürich\n".encode("latin-1")
    (r,) = CsvProcessor().process(csv_file(tmp_path, content))
    assert r.tables[0]["rows"][0] == ["José", "São Paulo"]


def test_csv_malformed_rows_recorded_not_merged(tmp_path):
    p = csv_file(tmp_path, b"a,b,c\n1,2,3\n4,5\n6,7,8,9\n10,11,12\n")
    (r,) = CsvProcessor().process(p)
    assert r.tables[0]["rows"] == [["1", "2", "3"], ["10", "11", "12"]]
    assert r.metadata["malformed_row_count"] == 2
    assert [m["line"] for m in r.metadata["malformed_rows"]] == [3, 4]


def test_csv_quoted_delimiters_and_newlines(tmp_path):
    p = csv_file(tmp_path, b'name,note\n"Smith, J","line1\nline2"\n')
    (r,) = CsvProcessor().process(p)
    assert r.tables[0]["rows"] == [["Smith, J", "line1\nline2"]]


def test_csv_header_only_and_blank_file(tmp_path):
    (r,) = CsvProcessor().process(csv_file(tmp_path, b"a,b\n"))
    assert r.status == PageStatus.ok and r.tables[0]["rows"] == []
    (r2,) = CsvProcessor().process(csv_file(tmp_path, b"\n\n", "b.csv"))
    assert r2.status == PageStatus.empty


@pytest.mark.parametrize("dup", [b"id,id,x\n1,2,3\n"])
def test_csv_duplicate_headers_flagged(tmp_path, dup):
    (r,) = CsvProcessor().process(csv_file(tmp_path, dup))
    assert r.metadata["duplicate_headers"] == ["id"]
