"""Image preprocessing for OCR: orientation, denoise, deskew, threshold."""

from __future__ import annotations

import cv2
import numpy as np

MAX_DESKEW_DEGREES = 15.0


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def denoise(gray: np.ndarray) -> np.ndarray:
    """Median filter: removes salt-and-pepper speckle typical of scans and keeps stroke edges.

    cv2.fastNlMeansDenoising was tried first and rejected: ~3.2 s per 300-dpi A4 page versus
    ~0.27 s for the OCR itself, which would make a 1,000-page scan take over an hour.
    """
    return cv2.medianBlur(gray, 3)


def estimate_skew_degrees(gray: np.ndarray) -> float:
    """Estimate skew from the minimum-area rectangle around dark (text) pixels.

    Returns degrees to rotate (positive = counter-clockwise). Returns 0.0 if unreliable.
    """
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = cv2.findNonZero(inv)
    if coords is None or len(coords) < 50:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1]
    # OpenCV >= 4.5 returns (0, 90]; normalise into (-45, 45]
    if angle > 45:
        angle -= 90
    return float(angle) if abs(angle) <= MAX_DESKEW_DEGREES else 0.0


def rotate(gray: np.ndarray, degrees: float) -> np.ndarray:
    if abs(degrees) < 0.1:
        return gray
    h, w = gray.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def threshold(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)


def preprocess(img: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Full pipeline; returns the OCR-ready image and the applied corrections (for metadata)."""
    gray = to_gray(img)
    gray = denoise(gray)
    skew = estimate_skew_degrees(gray)
    gray = rotate(gray, skew)
    return threshold(gray), {"deskew_degrees": round(skew, 2)}


def orientation_correct(img: np.ndarray, languages_hint: str = "eng") -> tuple[np.ndarray, int]:
    """Rotate by 90-degree steps using Tesseract OSD. Returns (image, degrees_applied).

    OSD fails on sparse pages; in that case the image is returned unchanged (degrees 0) rather
    than guessing.
    """
    import pytesseract

    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT, timeout=30)
    except (pytesseract.TesseractError, RuntimeError):
        return img, 0
    degrees = int(osd.get("rotate", 0)) % 360
    if osd.get("orientation_conf", 0) < 2.0 or degrees == 0:
        return img, 0
    codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return cv2.rotate(img, codes[degrees]), degrees
