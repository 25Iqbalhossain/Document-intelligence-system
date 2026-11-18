"""Pre-processing file validation: extension allowlist, size, and magic-byte sniffing.

The declared extension/MIME is never trusted; the signature must agree with the extension.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.processors.base import ValidationError

# extension -> (canonical MIME, signature check name)
ALLOWED: dict[str, tuple[str, str]] = {
    ".pdf": ("application/pdf", "pdf"),
    ".jpg": ("image/jpeg", "jpeg"),
    ".jpeg": ("image/jpeg", "jpeg"),
    ".png": ("image/png", "png"),
    ".tif": ("image/tiff", "tiff"),
    ".tiff": ("image/tiff", "tiff"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    ".xls": ("application/vnd.ms-excel", "xls"),
    ".csv": ("text/csv", "text"),
}

# Rejected on purpose: macro-enabled workbooks are not in ALLOWED (.xlsm/.xlsb).
MAX_ZIP_UNCOMPRESSED = 500 * 1024 * 1024  # zip-bomb guard for XLSX (it is a zip container)
MAX_ZIP_RATIO = 200


def _head(path: Path, n: int = 16) -> bytes:
    with path.open("rb") as f:
        return f.read(n)


def _is_signature(kind: str, head: bytes) -> bool:
    if kind == "pdf":
        return head.startswith(b"%PDF-")
    if kind == "jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if kind == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if kind == "tiff":
        return head[:4] in (b"II*\x00", b"MM\x00*")
    if kind == "xlsx":
        return head.startswith(b"PK\x03\x04")
    if kind == "xls":
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")  # OLE2 compound file
    if kind == "text":
        return b"\x00" not in head  # binary content is not CSV
    return False


def check_file(path: Path, max_bytes: int) -> tuple[str, str]:
    """Return (extension, canonical_mime) or raise ValidationError."""
    ext = path.suffix.lower()
    if ext not in ALLOWED:
        raise ValidationError("unsupported_type", f"Unsupported file type '{ext}'")
    if not path.is_file():
        raise ValidationError("not_found", "File does not exist")
    size = path.stat().st_size
    if size == 0:
        raise ValidationError("empty_file", "File is empty")
    if size > max_bytes:
        raise ValidationError("too_large", f"File is {size} bytes; limit is {max_bytes}")

    mime, kind = ALLOWED[ext]
    if not _is_signature(kind, _head(path)):
        raise ValidationError("signature_mismatch", f"Content does not look like a valid {ext} file")

    if kind == "xlsx":
        _check_zip(path)
    return ext, mime


def _check_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            total = sum(i.file_size for i in infos)
            compressed = max(sum(i.compress_size for i in infos), 1)
            names = {i.filename for i in infos}
    except zipfile.BadZipFile as exc:
        raise ValidationError("corrupt_file", "XLSX archive is corrupt") from exc
    if total > MAX_ZIP_UNCOMPRESSED or total / compressed > MAX_ZIP_RATIO:
        raise ValidationError("zip_bomb", "Archive expands to an unsafe size")
    if "xl/vbaProject.bin" in names:
        raise ValidationError("macros_not_allowed", "Workbooks containing macros are rejected")
    if "[Content_Types].xml" not in names:
        raise ValidationError("corrupt_file", "Not a valid XLSX workbook")
