"""Atomic file writes for ingestion output.

Scope & functionality
---------------------
Two atomic-write helpers used by the storage layer: ``write_text_atomic``
(a canonical Markdown/raw body to ``body_path``/``raw_path`` under
``/aeo/...``) and ``write_bytes_atomic`` (the raw upload bytes) — both via a
temp-file + ``os.replace`` so a body file is never observed partially
written.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def write_text_atomic(path: Path | str, content: str, *, encoding: str = "utf-8") -> str:
    """Write a text file atomically (temp file + ``os.replace``).

    Creates parent directories as needed. Returns the resolved path string.
    """
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd = None
    tmp_path: str | None = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent), prefix=".body_", suffix=".tmp",
        )
        with os.fdopen(tmp_fd, "w", encoding=encoding) as f:
            tmp_fd = None
            f.write(content)
        os.replace(tmp_path, str(target))
        tmp_path = None
        return str(target)
    finally:
        if tmp_fd is not None:
            os.close(tmp_fd)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def write_bytes_atomic(path: Path | str, content: bytes) -> str:
    """Write a binary file atomically (temp file + ``os.replace``)."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd = None
    tmp_path: str | None = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent), prefix=".raw_", suffix=".tmp",
        )
        with os.fdopen(tmp_fd, "wb") as f:
            tmp_fd = None
            f.write(content)
        os.replace(tmp_path, str(target))
        tmp_path = None
        return str(target)
    finally:
        if tmp_fd is not None:
            os.close(tmp_fd)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
