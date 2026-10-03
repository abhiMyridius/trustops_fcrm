"""Plain text / Markdown extractor — ``.txt``, ``.md``.

Scope & functionality
---------------------
Zero rendering invented: the whole (possibly front-matter-stripped) body
becomes a single ``Raw`` passthrough block — ``.md`` is assumed to already
be valid Markdown, ``.txt`` is wrapped as-is. Reuses
``core/encoding.py::EncodingDetector`` for the same BOM/UTF-16/cp1252
detection every text-bearing engine uses, so a binary file wearing a
``.txt``/``.md`` extension raises ``BinaryContent`` instead of silently
ingesting NUL bytes as ``COMPLETE`` (the key fix over the pre-model
extractor). Decoding is streamed with an incremental decoder so
``Limits.max_body_chars`` truncates by stopping the read, never by slicing
a fully materialised string.

YAML-ish front matter (a leading ``---`` block of simple ``key: value``
lines — no YAML dependency) is parsed into ``title``/``labels``/
``metadata["front_matter"]``; the remaining text is the document body.
"""

from __future__ import annotations

import codecs
from typing import TYPE_CHECKING, Any

from evoq_ingestion.core.codes import WarningCode
from evoq_ingestion.core.encoding import EncodingDetector
from evoq_ingestion.extractors.base import BaseExtractor, BinaryContent, EngineInfo
from evoq_ingestion.model.blocks import Raw
from evoq_ingestion.model.document import Document

if TYPE_CHECKING:
    from pathlib import Path

    from evoq_ingestion.core.limits import Deadline
    from evoq_ingestion.extractors.gate import FileFacts

__all__ = ["TextExtractor"]

_SAMPLE_BYTES = 64 * 1024
_CHUNK_BYTES = 1024 * 1024
_FRONT_MATTER_DELIM = "---"
_BOM_STRIP_ENCODINGS = frozenset({"utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"})


class TextExtractor(BaseExtractor):
    """``.txt``/``.md`` extractor: streamed decode + optional front matter."""

    info = EngineInfo("text", "2")
    supports = frozenset({".txt", ".md"})

    def _parse(self, path: Path, facts: FileFacts, deadline: Deadline) -> Document:
        """Decode *path* (streamed, capped) and split off any front matter."""
        doc = Document()
        if facts.size == 0:
            # Defensive only: the gate raises ``empty_file``/``corrupt_file``
            # before this extractor ever sees a 0-byte file in the real
            # pipeline; a direct/unit-test call still gets a valid empty doc.
            doc.add(Raw(markdown=""))
            return doc

        body, truncated_at, encoding_warnings = self._decode_streamed(path, deadline)
        for warning in encoding_warnings:
            doc.add_warning(warning)
        if truncated_at is not None:
            doc.add_warning(WarningCode.body_truncated, truncated_at)
        body = body.replace("\r\n", "\n").replace("\r", "\n")

        remainder, front_matter = _split_front_matter(body)
        if front_matter is not None:
            doc.add_warning(WarningCode.front_matter_parsed)
            title = front_matter.pop("title", None)
            labels = _extract_labels(front_matter)
            if title:
                doc.title = title
            if labels:
                doc.metadata["labels"] = labels
            if front_matter:
                doc.metadata["front_matter"] = front_matter
            body = remainder

        doc.add(Raw(markdown=body))
        return doc

    # ------------------------------------------------------------- decoding
    def _decode_streamed(
        self, path: Path, deadline: Deadline
    ) -> tuple[str, int | None, tuple[str, ...]]:
        """Detect encoding on the head, then stream-decode with a cap.

        Returns ``(text, truncated_at_chars, encoding_warnings)`` —
        ``truncated_at_chars`` is ``None`` unless ``Limits.max_body_chars``
        was hit, in which case reading stops immediately (no full
        read-then-slice); ``encoding_warnings`` carries the detector's own
        warnings (e.g. ``encoding_fallback:cp1252``) so they land in
        ``Document.warnings`` instead of being silently dropped.
        """
        cap = self.options.limits.max_body_chars
        detector = EncodingDetector()
        with path.open("rb") as handle:
            head = handle.read(_SAMPLE_BYTES)
            result = detector.detect(head)
            if result.is_binary:
                raise BinaryContent(f"{path.name} contains binary data")

            decoder = codecs.getincrementaldecoder(result.encoding)(errors="replace")
            parts: list[str] = []
            total = 0
            truncated_at: int | None = None
            chunk: bytes | None = head
            while True:
                deadline.check()
                if chunk is None:
                    chunk = handle.read(_CHUNK_BYTES)
                if not chunk:
                    tail = decoder.decode(b"", final=True).replace("\x00", "")
                    if tail:
                        parts.append(tail)
                        total += len(tail)
                    break
                piece = decoder.decode(chunk).replace("\x00", "")
                chunk = None
                if piece:
                    remaining = cap - total
                    if remaining <= 0:
                        truncated_at = total
                        break
                    if len(piece) > remaining:
                        parts.append(piece[:remaining])
                        total += remaining
                        truncated_at = total
                        break
                    parts.append(piece)
                    total += len(piece)

        text = "".join(parts)
        if result.bom and result.encoding in _BOM_STRIP_ENCODINGS and text.startswith("﻿"):
            text = text[1:]
        return text, truncated_at, result.warnings


# --------------------------------------------------------------- front matter
def _split_front_matter(body: str) -> tuple[str, dict[str, Any] | None]:
    """Split a leading ``---`` / ``key: value`` / ``---`` block off *body*.

    Returns ``(remaining_body, parsed_dict)``; ``parsed_dict`` is ``None``
    when *body* has no front-matter block (``remaining_body`` is then
    *body* unchanged).
    """
    if not body.startswith(f"{_FRONT_MATTER_DELIM}\n"):
        return body, None
    lines = body.split("\n")
    end_index = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == _FRONT_MATTER_DELIM),
        None,
    )
    if end_index is None:
        return body, None
    data: dict[str, Any] = {}
    for line in lines[1:end_index]:
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = _unquote(value.strip())
    remainder = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    return remainder, data


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _extract_labels(front_matter: dict[str, Any]) -> list[str]:
    """Pop ``tags``/``labels`` (comma-separated or ``[a, b]``) as a list."""
    raw = front_matter.pop("tags", None) or front_matter.pop("labels", None)
    if not raw:
        return []
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip("\"'") for item in text.split(",") if item.strip()]
