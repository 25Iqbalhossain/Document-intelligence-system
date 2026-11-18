import pytest

from app.processors.base import ValidationError
from app.processors.file_checks import check_file
from app.processors.registry import get_processor
from tests.unit import factories as f

MB = 1024 * 1024


def code(exc_info):
    return exc_info.value.code


def test_accepts_valid_pdf(tmp_path):
    p = f.digital_pdf(tmp_path / "a.pdf", ["hello world invoice text here"])
    assert check_file(p, MB) == (".pdf", "application/pdf")


def test_rejects_unsupported_extension(tmp_path):
    p = tmp_path / "a.exe"
    p.write_bytes(b"MZ....")
    with pytest.raises(ValidationError) as e:
        check_file(p, MB)
    assert code(e) == "unsupported_type"


def test_rejects_empty_and_oversize(tmp_path):
    empty = tmp_path / "e.pdf"
    empty.write_bytes(b"")
    with pytest.raises(ValidationError) as e:
        check_file(empty, MB)
    assert code(e) == "empty_file"
    big = f.digital_pdf(tmp_path / "b.pdf", ["x" * 40])
    with pytest.raises(ValidationError) as e:
        check_file(big, 100)
    assert code(e) == "too_large"


def test_signature_must_match_extension(tmp_path):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"MZ\x90\x00 this is an executable")
    with pytest.raises(ValidationError) as e:
        check_file(p, MB)
    assert code(e) == "signature_mismatch"


def test_png_renamed_to_pdf_rejected(tmp_path):
    img = f.skewed_image(tmp_path / "x.png", ["hi"], angle=0)
    bad = tmp_path / "x.pdf"
    bad.write_bytes(img.read_bytes())
    with pytest.raises(ValidationError) as e:
        check_file(bad, MB)
    assert code(e) == "signature_mismatch"


def test_macro_workbook_rejected(tmp_path):
    p = f.macro_xlsx(tmp_path / "m.xlsx")
    with pytest.raises(ValidationError) as e:
        check_file(p, MB)
    assert code(e) == "macros_not_allowed"


def test_corrupt_xlsx_zip_rejected(tmp_path):
    p = tmp_path / "c.xlsx"
    p.write_bytes(b"PK\x03\x04" + b"garbage" * 50)
    with pytest.raises(ValidationError) as e:
        check_file(p, MB)
    assert code(e) == "corrupt_file"


def test_truncated_pdf_rejected_by_processor(tmp_path):
    good = f.digital_pdf(tmp_path / "g.pdf", ["some reasonably long text for the page"] * 3)
    bad = tmp_path / "t.pdf"
    bad.write_bytes(good.read_bytes()[:150])
    with pytest.raises(ValidationError) as e:
        get_processor(bad, MB)
    assert code(e) == "corrupt_file"


def test_encrypted_pdf_rejected(tmp_path):
    import pymupdf

    src = f.digital_pdf(tmp_path / "s.pdf", ["secret text that is long enough"])
    doc = pymupdf.open(src)
    enc = tmp_path / "enc.pdf"
    doc.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    with pytest.raises(ValidationError) as e:
        get_processor(enc, MB)
    assert code(e) == "encrypted"


def test_registry_picks_processor_by_type(tmp_path):
    assert type(get_processor(f.xlsx(tmp_path / "a.xlsx"), MB)).__name__ == "ExcelProcessor"
    csv = tmp_path / "a.csv"
    csv.write_text("a,b\n1,2\n")
    assert type(get_processor(csv, MB)).__name__ == "CsvProcessor"
