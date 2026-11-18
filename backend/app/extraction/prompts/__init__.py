"""Versioned prompt files. The version string is stored with every extraction."""

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache
def load_prompt(version: str) -> str:
    if not version.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise ValueError(f"Invalid prompt version '{version}'")
    path = _DIR / f"{version}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt version '{version}' not found in {_DIR}")
    return path.read_text(encoding="utf-8")
