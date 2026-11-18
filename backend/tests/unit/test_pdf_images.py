import pytest

from app.processors.base import PageKind, PageStatus, ProcessingError
from app.processors.images.processor import ImageProcessor
from app.processors.pdf.processor import PdfProcessor
from tests.unit import factories as f

LINES = ["Invoice Number INV-2025-0042", "Total 1150.00 BDT", "Supplier Rahim Traders"]


def test_digital_pdf_uses_native_text_and_preserves_page_numbers(tmp_path):
    p = f.digital_pdf(tmp_path / "d.pdf", [f"Page {i} content line with enough chars" for i in range(1, 6)])
    res = list(PdfProcessor().process(p))
    assert [r.page_number for r in res] == [1, 2, 3, 4, 5]
    assert all(r.kind == PageKind.digital and r.source == "native" and r.ocr_confidence is None for r in res)
    assert "Page 3 content" in res[2].text


def test_page_batches_are_independent_and_complete(tmp_path):
    p = f.digital_pdf(tmp_path / "d.pdf", [f"Batch page {i} has plenty of text" for i in range(1, 46)])
    proc = PdfProcessor()
    got = []
    for start in range(1, 46, 20):
        got += [r.page_number for r in proc.process(p, start, start + 19)]
    assert got == list(range(1, 46))


def test_bad_page_range_raises(tmp_path):
    p = f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough here"])
    with pytest.raises(ProcessingError) as e:
        list(PdfProcessor().process(p, 5, 9))
    assert e.value.code == "bad_page_range"


def test_scanned_pdf_goes_through_ocr_with_confidence(tmp_path):
    p = f.scanned_pdf(tmp_path / "s.pdf", LINES)
    (r,) = PdfProcessor().process(p)
    assert r.kind == PageKind.scanned and r.source == "ocr" and r.status == PageStatus.ok
    assert r.ocr_confidence is not None and r.ocr_confidence > 50
    assert "INV-2025-0042" in r.text.replace(" ", "")
    assert "1150.00" in r.text


def test_blank_scanned_page_is_empty_not_failed_and_not_skipped(tmp_path):
    p = f.scanned_pdf(tmp_path / "s.pdf", LINES, extra_blank=True)
    res = list(PdfProcessor().process(p))
    assert len(res) == 2
    assert res[1].status == PageStatus.empty


def test_ocr_failure_is_recorded_per_page_and_batch_continues(tmp_path):
    class Boom:
        def recognize(self, image):
            raise ProcessingError("ocr_failed", "engine exploded")

    p = f.scanned_pdf(tmp_path / "s.pdf", LINES, extra_blank=True)
    res = list(PdfProcessor(ocr=Boom()).process(p))
    assert [r.status for r in res] == [PageStatus.failed, PageStatus.failed]
    assert "ocr_failed" in res[0].error


def test_skewed_image_ocr(tmp_path):
    p = f.skewed_image(tmp_path / "k.png", LINES, angle=4.0)
    (r,) = ImageProcessor().process(p)
    assert r.status == PageStatus.ok and r.kind == PageKind.image
    assert "INV-2025-0042" in r.text.replace(" ", "")
    assert r.ocr_confidence is not None


def test_rotated_image_is_corrected(tmp_path):
    p = f.skewed_image(tmp_path / "r.png", LINES, angle=0, rotate90=90)
    (r,) = ImageProcessor().process(p)
    # Orientation detection is best-effort; assert the outcome, and report which path was taken.
    assert r.status == PageStatus.ok
    assert "1150.00" in r.text, f"rotation not recovered, metadata={r.metadata}"


def test_jpeg_and_tiff_supported(tmp_path):
    img = f.text_image(LINES)
    jpg, tif = tmp_path / "a.jpg", tmp_path / "a.tiff"
    img.save(jpg)
    img.save(tif)
    for path in (jpg, tif):
        (r,) = ImageProcessor().process(path)
        assert "Rahim" in r.text, path


def test_multipage_tiff_yields_page_per_frame(tmp_path):
    a, b = f.text_image(LINES), f.text_image(["Second page total 77.00"])
    tif = tmp_path / "m.tiff"
    a.save(tif, save_all=True, append_images=[b])
    res = list(ImageProcessor().process(tif))
    assert [r.page_number for r in res] == [1, 2]
    assert "77.00" in res[1].text


def test_corrupt_image_rejected(tmp_path):
    p = tmp_path / "bad.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"not really png data" * 10)
    from app.processors.base import ValidationError

    with pytest.raises(ValidationError):
        ImageProcessor().validate(p)


def test_noisy_scan_still_readable(tmp_path):
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(0)
    arr = np.array(f.text_image(LINES))
    mask = rng.random(arr.shape)
    arr[mask < 0.02] = 0      # pepper
    arr[mask > 0.98] = 255    # salt
    p = tmp_path / "noisy.png"
    Image.fromarray(arr).save(p)
    (r,) = ImageProcessor().process(p)
    assert r.status == PageStatus.ok
    assert "1150.00" in r.text and "INV-2025-0042" in r.text.replace(" ", "")
