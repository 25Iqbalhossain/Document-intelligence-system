"""Generators for sample files used by processor tests."""
from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

FONT = ImageFont.load_default(size=40)


def text_image(lines: list[str], size=(1600, 900)) -> Image.Image:
    img = Image.new("L", size, 255)
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        d.text((80, 80 + i * 90), ln, fill=0, font=FONT)
    return img


def digital_pdf(path: Path, pages: list[str]) -> Path:
    doc = pymupdf.open()
    for t in pages:
        p = doc.new_page()
        p.insert_text((72, 100), t, fontsize=12)
    doc.save(path)
    doc.close()
    return path


def scanned_pdf(path: Path, lines: list[str], extra_blank: bool = False) -> Path:
    doc = pymupdf.open()
    img_path = path.with_suffix(".page.png")
    text_image(lines).save(img_path)
    p = doc.new_page(width=595, height=842)
    p.insert_image(pymupdf.Rect(20, 20, 575, 400), filename=str(img_path))
    if extra_blank:
        doc.new_page()
    doc.save(path)
    doc.close()
    img_path.unlink()
    return path


def skewed_image(path: Path, lines: list[str], angle: float = 5.0, rotate90: int = 0) -> Path:
    img = text_image(lines).rotate(angle, expand=True, fillcolor=255)
    if rotate90:
        img = img.rotate(rotate90, expand=True)
    img.save(path)
    return path


def xlsx(path: Path) -> Path:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoices"
    ws["A1"] = "ACME Ltd - Invoice Register"  # title banner above the header
    ws.merge_cells("A1:D1")
    ws.append([])
    ws.append(["Invoice No", "Date", "Supplier", "Total"])
    ws.append(["INV-001", "2025-01-05", "Rahim Traders", 1150.5])
    ws.append(["INV-002", "2025-01-06", "Karim & Sons", 0])
    ws2 = wb.create_sheet("Lines")
    ws2.append(["Invoice No", "Item", "Qty"])
    ws2.append(["INV-001", "Cement", 10])
    ws2.merge_cells("A3:A4")
    ws2["A3"] = "INV-002"
    ws2["B3"], ws2["C3"] = "Sand", 4
    ws2["B4"], ws2["C4"] = "Gravel", 2
    wb.create_sheet("Empty")
    wb.save(path)
    return path


def xls(path: Path) -> Path:
    import xlwt

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Legacy")
    ws.write(0, 0, "Invoice No")
    ws.write(0, 1, "Total")
    ws.write(1, 0, "OLD-1")
    ws.write(1, 1, 99.5)
    ws.write_merge(2, 3, 0, 0, "OLD-2")
    ws.write(2, 1, 10)
    ws.write(3, 1, 20)
    wb.save(path)
    return path


def macro_xlsx(path: Path) -> Path:
    xlsx(path)
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("xl/vbaProject.bin", b"fake")
    return path


def numpy_blank() -> np.ndarray:
    return np.full((200, 200), 255, dtype=np.uint8)
