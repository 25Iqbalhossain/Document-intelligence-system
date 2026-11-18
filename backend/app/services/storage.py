"""Private file storage. Keys are server-generated; user filenames never reach the filesystem."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, Protocol

from app.processors.base import ValidationError
from app.processors.file_checks import ALLOWED

CHUNK = 1024 * 1024


class StorageBackend(Protocol):
    def save(self, stream: BinaryIO, extension: str, max_bytes: int) -> tuple[str, str, int]: ...
    def path(self, key: str) -> Path: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        """Resolve a key and refuse anything that escapes the storage root."""
        candidate = (self.root / key).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise ValidationError("path_traversal", "Invalid storage key")
        return candidate

    def save(self, stream: BinaryIO, extension: str, max_bytes: int) -> tuple[str, str, int]:
        """Stream to disk while hashing; abort as soon as the size limit is exceeded.

        Returns (key, sha256, size_bytes). Only allowlisted extensions are ever used in a key.
        """
        extension = extension.lower()
        if extension not in ALLOWED:
            raise ValidationError("unsupported_type", f"Unsupported file type '{extension}'")
        name = uuid.uuid4().hex
        key = f"{name[:2]}/{name}{extension}"
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        digest, size = hashlib.sha256(), 0
        try:
            with tmp.open("wb") as out:
                for chunk in iter_chunks(stream):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValidationError("too_large", f"File exceeds the {max_bytes}-byte limit")
                    digest.update(chunk)
                    out.write(chunk)
            os.replace(tmp, target)  # atomic: readers never see a partial file
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return key, digest.hexdigest(), size

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)


def iter_chunks(stream: BinaryIO) -> Iterator[bytes]:
    while chunk := stream.read(CHUNK):
        yield chunk
