"""Deterministic text-encoding detection for CSV/TXT/MD uploads.

Scope & functionality
---------------------
``EncodingDetector.detect(head)`` inspects the first bytes of a file and
decides, in order: BOM (``utf-8-sig`` / UTF-16 / UTF-32), NUL-alternation
(BOM-less UTF-16), strict UTF-8 on a sample, else ``cp1252`` with an
``encoding_fallback:cp1252`` warning. It also decides whether the content is
binary (NUL or non-printable ratio above ``binary_threshold``) so a
``.txt`` that is really an executable becomes ``binary_content`` instead of
NUL bytes in Mongo.

``decode(data)`` applies the decision to a byte string and **never raises
and never returns NUL characters** (property-tested): undecodable bytes are
replaced, NULs are dropped. Streaming readers use ``detect`` once on the
head and then ``codecs.getincrementaldecoder(result.encoding)``.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field

from evoq_ingestion.core.codes import WarningCode, with_detail

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_PRINTABLE_CONTROL = frozenset(b"\t\n\r\x0c\x0b")


@dataclass(frozen=True, slots=True)
class EncodingResult:
    """Outcome of ``EncodingDetector.detect``."""

    encoding: str
    bom: bool = False
    is_binary: bool = False
    non_printable_ratio: float = 0.0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DecodedText:
    """Outcome of ``EncodingDetector.decode`` — text is NUL-free."""

    text: str
    result: EncodingResult
    replaced_chars: int = 0
    warnings: tuple[str, ...] = field(default=())


class EncodingDetector:
    """Detect the encoding of a text file head and decode bytes safely."""

    def __init__(self, *, binary_threshold: float = 0.30, sample_bytes: int = 64 * 1024) -> None:
        """Construct a detector; ``binary_threshold`` and ``sample_bytes`` bound the sniff."""
        if not 0.0 < binary_threshold <= 1.0:
            raise ValueError("binary_threshold must be in (0, 1]")
        if sample_bytes <= 0:
            raise ValueError("sample_bytes must be positive")
        self._binary_threshold = binary_threshold
        self._sample_bytes = sample_bytes

    # ------------------------------------------------------------------ detect
    def detect(self, head: bytes) -> EncodingResult:
        """Decide the encoding from the first bytes of a file. Never raises."""
        sample = bytes(head[: self._sample_bytes])
        if not sample:
            return EncodingResult(encoding="utf-8")

        for bom, encoding in _BOMS:
            if sample.startswith(bom):
                payload = sample[len(bom) :]
                ratio = self._non_printable_ratio_decoded(payload, encoding)
                return EncodingResult(
                    encoding=encoding,
                    bom=True,
                    is_binary=ratio > self._binary_threshold,
                    non_printable_ratio=ratio,
                )

        utf16 = self._sniff_utf16(sample)
        if utf16 is not None:
            ratio = self._non_printable_ratio_decoded(sample, utf16)
            return EncodingResult(
                encoding=utf16,
                is_binary=ratio > self._binary_threshold,
                non_printable_ratio=ratio,
            )

        raw_ratio = self._non_printable_ratio_bytes(sample)
        if raw_ratio > self._binary_threshold:
            return self._binary(raw_ratio)

        if self._is_strict_utf8(sample):
            return EncodingResult(encoding="utf-8", non_printable_ratio=raw_ratio)

        # Not UTF-8: cp1252 prose has a few accented bytes; compressed/random
        # binary has roughly half its bytes >= 0x80.
        high_ratio = sum(1 for b in sample if b >= 0x80) / len(sample)
        if high_ratio > self._binary_threshold:
            return self._binary(max(raw_ratio, high_ratio))

        return EncodingResult(
            encoding="cp1252",
            non_printable_ratio=raw_ratio,
            warnings=(with_detail(WarningCode.encoding_fallback, "cp1252"),),
        )

    @staticmethod
    def _binary(ratio: float) -> EncodingResult:
        return EncodingResult(
            encoding="utf-8",
            is_binary=True,
            non_printable_ratio=ratio,
            warnings=(str(WarningCode.binary_content),),
        )

    # ------------------------------------------------------------------ decode
    def decode(self, data: bytes, result: EncodingResult | None = None) -> DecodedText:
        """Decode *data* with the detected encoding; output has no NULs. Never raises."""
        result = result or self.detect(data)
        payload = bytes(data)
        if result.bom:
            for bom, encoding in _BOMS:
                if encoding == result.encoding and payload.startswith(bom):
                    payload = payload[len(bom) :]
                    break
        try:
            text = payload.decode(result.encoding, errors="replace")
        except LookupError:  # unknown codec name — cannot happen for our own results
            text = payload.decode("utf-8", errors="replace")
        replaced = text.count("�")
        nul_free = text.replace("\x00", "")
        # utf-8-sig strips the BOM itself; other BOM codecs were handled above.
        if result.encoding != "utf-8-sig" and nul_free.startswith("﻿"):
            nul_free = nul_free[1:]
        return DecodedText(
            text=nul_free,
            result=result,
            replaced_chars=replaced,
            warnings=result.warnings,
        )

    # ----------------------------------------------------------------- helpers
    def _non_printable_ratio_bytes(self, sample: bytes) -> float:
        if not sample:
            return 0.0
        bad = sum(1 for b in sample if (b < 0x20 and b not in _PRINTABLE_CONTROL) or b == 0x7F)
        return bad / len(sample)

    def _non_printable_ratio_decoded(self, payload: bytes, encoding: str) -> float:
        if not payload:
            return 0.0
        text = payload.decode(encoding, errors="replace")
        if not text:
            return 0.0
        bad = sum(1 for ch in text if _is_non_printable(ch))
        return bad / len(text)

    @staticmethod
    def _sniff_utf16(sample: bytes) -> str | None:
        """BOM-less UTF-16: NULs alternating in even or odd positions."""
        if len(sample) < 4:
            return None
        even = sample[0::2]
        odd = sample[1::2]
        even_nul = even.count(0) / len(even)
        odd_nul = odd.count(0) / len(odd)
        # ASCII-heavy UTF-16-LE has NUL in odd positions, BE in even ones.
        if odd_nul > 0.30 and even_nul < 0.05:
            return "utf-16-le"
        if even_nul > 0.30 and odd_nul < 0.05:
            return "utf-16-be"
        return None

    @staticmethod
    def _is_strict_utf8(sample: bytes) -> bool:
        """Strict UTF-8 check tolerant of a multi-byte sequence cut at the sample end."""
        try:
            sample.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            # A sequence that starts within the last 3 bytes and is merely cut
            # off by the sample boundary is not evidence against UTF-8.
            truncated_tail = exc.start >= len(sample) - 3 and exc.end >= len(sample)
            return truncated_tail and "unexpected end" in exc.reason
        return True


def _is_non_printable(ch: str) -> bool:
    code = ord(ch)
    if code == 0x7F or code == 0xFFFD:
        return True
    return code < 0x20 and ch not in "\t\n\r\x0b\x0c"
