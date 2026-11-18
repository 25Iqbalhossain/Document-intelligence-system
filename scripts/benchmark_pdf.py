"""Measure peak memory and time of batched PDF processing. Prints measured values only.

Usage: python scripts/benchmark_pdf.py [--pages 100 300 1000] [--batch 20] [--scanned-sample 10]
Each case runs in a fresh subprocess so ru_maxrss reflects that run alone.
"""
from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"

CHILD = r"""
import json, resource, sys, time
sys.path.insert(0, sys.argv[4])
from pathlib import Path
from app.processors.pdf.processor import PdfProcessor
path, batch, total = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
proc = PdfProcessor()
t = time.perf_counter(); n = ok = 0; chars = 0; confs = []
for start in range(1, total + 1, batch):
    for r in proc.process(path, start, min(start + batch - 1, total)):
        n += 1; ok += r.status.value == "ok"; chars += len(r.text)
        if r.ocr_confidence is not None: confs.append(r.ocr_confidence)
el = time.perf_counter() - t
print(json.dumps({"pages": n, "ok": ok, "chars": chars, "seconds": round(el, 2),
    "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    "mean_ocr_conf": round(sum(confs)/len(confs), 1) if confs else None}))
"""


def make_digital(path: Path, pages: int) -> None:
    import pymupdf
    doc = pymupdf.open()
    body = "Invoice line item description with quantity and amount. " * 30
    for i in range(pages):
        p = doc.new_page()
        p.insert_textbox(pymupdf.Rect(40, 40, 555, 800), f"Page {i + 1}\n{body}", fontsize=9)
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def make_scanned(path: Path, pages: int) -> None:
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=40)
    img = Image.new("L", (1600, 900), 255)
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(["Invoice Number INV-2025-0042", "Total 1150.00 BDT", "Supplier Rahim Traders"]):
        d.text((80, 80 + i * 90), ln, fill=0, font=font)
    png = path.with_suffix(".png")
    img.save(png)
    doc = pymupdf.open()
    for _ in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_image(pymupdf.Rect(20, 20, 575, 400), filename=str(png))
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def run_case(path: Path, batch: int, pages: int) -> dict:
    out = subprocess.run([sys.executable, "-c", CHILD, str(path), str(batch), str(pages), str(BACKEND)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, nargs="+", default=[100, 300, 1000])
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--scanned-sample", type=int, default=10)
    a = ap.parse_args()
    results = {"batch_size": a.batch, "digital": [], "scanned_sample": None}
    with tempfile.TemporaryDirectory() as td:
        for n in a.pages:
            p = Path(td) / f"d{n}.pdf"
            make_digital(p, n)
            r = run_case(p, a.batch, n) | {"file_mb": round(p.stat().st_size / 1e6, 2)}
            results["digital"].append(r)
        p = Path(td) / "scan.pdf"
        make_scanned(p, a.scanned_sample)
        results["scanned_sample"] = run_case(p, a.batch, a.scanned_sample)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
