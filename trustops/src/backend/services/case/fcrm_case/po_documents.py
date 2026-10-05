"""What the PO's upload accepts and where the demo documents come from.

An upload is checked before a single byte reaches the object store: the file
name is reduced to a safe base name, its extension must be on the allow-list
(``UPLOAD_ALLOWED_EXTENSIONS``), its size under the cap (``UPLOAD_MAX_BYTES``)
and its first bytes must be what the extension claims (a ``.pdf`` that is not a
PDF is refused, not stored). The content type recorded is the one the extension
implies, never the client's header. Pure checks here; the service does the I/O.

The demo case's documents are not uploaded from a laptop: the committed PDFs
under ``evals/fixtures/documents`` are named in a JSON body
(``{"demo_files": [...]}``) and copied server-side. :class:`DemoFixtures` reads
them from a directory when ``DEMO_DOCUMENTS_DIR`` names one (development), else
from the object store's ``uploads/_fixtures/`` prefix that ``make seed`` fills.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from fcrm_common.errors import NotFoundError

from fcrm_case.po_errors import FileRefusedError, PayloadTooLargeError

logger = logging.getLogger(__name__)

#: Extension -> the content type recorded for it.
CONTENT_TYPES: Final[dict[str, str]] = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".txt": "text/plain",
}

#: The demo case's two intake documents (the third, the scope letter, is
#: uploaded later as the answer to a question).
DEMO_INTAKE_FILES: Final[tuple[str, ...]] = (
    "business_case_remittance_MX.pdf",
    "payout_partner_contract.pdf",
)

FIXTURE_PREFIX: Final = "_fixtures"

_OOXML: Final = frozenset({".docx", ".xlsx", ".pptx"})
_TEXT: Final = frozenset({".csv", ".md", ".txt"})
_UNSAFE_NAME: Final = re.compile(r"[^A-Za-z0-9._ -]+")
_MAX_NAME_CHARS: Final = 120
_SNIFF_BYTES: Final = 8192
_READ_CHUNK: Final = 1024 * 1024


@dataclass(frozen=True)
class IncomingFile:
    """One file of an upload, read in full (the cap bounds it)."""

    name: str
    data: bytes


class Readable(Protocol):
    """What reading an uploaded file needs (``starlette.UploadFile``)."""

    async def read(self, size: int = -1, /) -> bytes:
        """Up to ``size`` bytes."""
        ...


def clean_file_name(raw: str) -> str:
    """The base name of ``raw`` with anything outside letters, digits and
    ``. _ -`` replaced by ``_`` and runs of spaces collapsed, at most 120
    characters (the extension kept). A path with directories in it loses
    them.

    Raises:
        FileRefusedError: Nothing usable is left, or there is no extension.
    """
    base = re.split(r"[\\/]", raw.strip())[-1]
    safe = _UNSAFE_NAME.sub("_", base)
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    if not safe or safe.startswith("."):
        raise FileRefusedError("The file needs a name.")
    stem, dot, extension = safe.rpartition(".")
    if not dot or not stem:
        raise FileRefusedError(f"{safe} has no file extension.")
    stem = stem[: _MAX_NAME_CHARS - len(extension) - 1]
    return f"{stem}.{extension}"


def extension_of(name: str) -> str:
    """The lower-case ``.ext`` of ``name``."""
    return Path(name).suffix.lower()


def _sniff_problem(extension: str, data: bytes) -> str:
    """What is wrong with ``data`` for its ``extension``, or ``""``."""
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        return "is not a PDF"
    if extension in _OOXML and not data.startswith(b"PK\x03\x04"):
        return "is not an Office document"
    if extension in _TEXT:
        head = data[:_SNIFF_BYTES]
        if b"\x00" in head:
            return "is not a text file"
        try:
            head.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            # The cut may land inside a multi-byte character: only a failure
            # well before the end of the sample counts.
            if exc.start < len(head) - 4:
                return "is not UTF-8 text"
    return ""


def check_file(
    name: str, data: bytes, *, allowed: Sequence[str], max_bytes: int
) -> str:
    """Accept ``data`` as the file ``name`` or refuse it; returns the content
    type to record.

    Args:
        name: A name already cleaned by :func:`clean_file_name`.
        data: The file's bytes.
        allowed: The allowed extensions (``.pdf``...).
        max_bytes: The per-file size cap.

    Raises:
        PayloadTooLargeError: ``data`` is over ``max_bytes``.
        FileRefusedError: The extension is not allowed, the file is empty or
            its first bytes are not what the extension claims.
    """
    extension = extension_of(name)
    if extension not in {a.lower() for a in allowed} or extension not in CONTENT_TYPES:
        listed = ", ".join(sorted(a.lower() for a in allowed))
        raise FileRefusedError(f"{name}: only {listed} files can be uploaded.")
    if not data:
        raise FileRefusedError(f"{name} is empty.")
    if len(data) > max_bytes:
        raise PayloadTooLargeError(
            f"{name} is over the {max_bytes // (1024 * 1024)} MB limit."
        )
    problem = _sniff_problem(extension, data)
    if problem:
        raise FileRefusedError(f"{name} {problem}.")
    return CONTENT_TYPES[extension]


async def read_limited(file: Readable, *, name: str, max_bytes: int) -> bytes:
    """Read ``file`` in chunks, stopping with a refusal the moment it is over
    ``max_bytes`` (so a huge file is never held in memory).

    Raises:
        PayloadTooLargeError: The file is over ``max_bytes``.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                f"{name} is over the {max_bytes // (1024 * 1024)} MB limit."
            )
        chunks.append(chunk)


class DemoFixtures(Protocol):
    """Where the committed demo documents are read from."""

    async def read(self, name: str) -> bytes:
        """The bytes of the demo document ``name``.

        Raises:
            fcrm_common.errors.NotFoundError: There is no such demo document.
        """
        ...


class DirectoryFixtures:
    """Demo documents from a directory (``DEMO_DOCUMENTS_DIR``)."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    async def read(self, name: str) -> bytes:
        path = self._directory / clean_file_name(name)

        def _read() -> bytes:
            try:
                return path.read_bytes()
            except FileNotFoundError as exc:
                raise NotFoundError(f"no demo document named {name}") from exc

        return await asyncio.to_thread(_read)


class ObjectStoreReader(Protocol):
    """What :class:`ObjectFixtures` needs of the object store."""

    async def get_bytes(self, bucket: str, key: str) -> bytes:
        """The object at ``bucket``/``key``."""
        ...


class ObjectFixtures:
    """Demo documents from ``<uploads bucket>/_fixtures/`` (staged by
    ``make seed``).
    """

    def __init__(self, store: ObjectStoreReader, bucket: str) -> None:
        self._store = store
        self._bucket = bucket

    async def read(self, name: str) -> bytes:
        try:
            return await self._store.get_bytes(
                self._bucket, f"{FIXTURE_PREFIX}/{clean_file_name(name)}"
            )
        except NotFoundError as exc:
            raise NotFoundError(f"no demo document named {name}") from exc
