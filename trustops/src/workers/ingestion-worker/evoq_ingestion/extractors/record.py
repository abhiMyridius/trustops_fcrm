"""``ExtractionRecord`` — the typed ``extraction`` dict every engine emits.

Scope & functionality
---------------------
Replaces the hand-built ``extraction`` dicts (which drifted per engine —
``raw_hash`` never computed, ``duration_ms`` hard-coded ``0`` for Jira and
Confluence, ``profile_version`` absent). ``to_dict()`` emits **every**
contract key for every engine: ``engine``, ``engine_version``,
``html2md_version``, ``adf2md_version``, ``profile_version``,
``fallback_used``, ``warnings``, ``duration_ms``, ``raw_hash``, plus the
additive ``failure_code``, ``failure_message``, ``unit_failures``,
``scanned_page_fraction``, ``is_scanned_pdf`` and ``front_matter``.

``raw_hash`` is ``"sha256:<hex>"`` of the raw input — file bytes, the HTML
body, or the canonical (``sort_keys``) JSON of an ADF tree — and is never
``None``; use ``raw_hash_bytes`` / ``raw_hash_text`` / ``raw_hash_json``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from evoq_ingestion.core.codes import FailureCode, WarningCode, with_detail
from evoq_ingestion.core.profile import PROFILE_VERSION
from evoq_ingestion.model.document import UnitFailure

RAW_HASH_EMPTY = "sha256:" + hashlib.sha256(b"").hexdigest()


def raw_hash_bytes(data: bytes) -> str:
    """``sha256:<hex>`` of raw bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def raw_hash_text(text: str) -> str:
    """``sha256:<hex>`` of UTF-8 encoded text (HTML bodies)."""
    return raw_hash_bytes(text.encode("utf-8", "surrogatepass"))


def raw_hash_json(value: object) -> str:
    """``sha256:<hex>`` of the canonical JSON form (``sort_keys``, compact) of *value*."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return raw_hash_bytes(canonical.encode("utf-8"))


@dataclass(slots=True)
class ExtractionRecord:
    """Typed ``extraction`` block; ``to_dict`` always emits every key."""

    engine: str
    engine_version: str
    raw_hash: str = RAW_HASH_EMPTY
    duration_ms: int = 0
    fallback_used: bool = False
    warnings: list[str] = field(default_factory=list)
    html2md_version: str | None = None
    adf2md_version: str | None = None
    profile_version: int = PROFILE_VERSION
    failure_code: str | None = None
    failure_message: str | None = None
    unit_failures: list[UnitFailure] = field(default_factory=list)
    scanned_page_fraction: float | None = None
    is_scanned_pdf: bool = False
    front_matter: dict[str, Any] | None = None

    # ---------------------------------------------------------------- mutate
    def add_warning(self, code: WarningCode | FailureCode | str, detail: object = None) -> str:
        """Append ``code`` / ``code:detail`` once; returns the string written."""
        if isinstance(code, WarningCode | FailureCode):
            warning = with_detail(code, detail)
        else:
            warning = str(code) if detail is None or detail == "" else f"{code}:{detail}"
        if warning not in self.warnings:
            self.warnings.append(warning)
        return warning

    def extend_warnings(self, warnings: list[str] | tuple[str, ...]) -> None:
        """Append warnings preserving order, skipping duplicates."""
        for warning in warnings:
            self.add_warning(warning)

    def mark_failed(self, code: FailureCode | str, message: str) -> None:
        """Record the terminal failure and make sure the code is also a warning."""
        self.failure_code = str(code)
        self.failure_message = message
        self.add_warning(str(code))

    def mark_fallback(self, from_engine: str) -> None:
        """Record that an earlier engine failed and this one took over."""
        self.fallback_used = True
        fallback = with_detail(WarningCode.fallback, from_engine)
        if fallback not in self.warnings:
            self.warnings.insert(0, fallback)

    # ------------------------------------------------------------- serialise
    def to_dict(self) -> dict[str, Any]:
        """Contract shape — every key present for every engine."""
        return {
            "engine": self.engine,
            "engine_version": self.engine_version,
            "html2md_version": self.html2md_version,
            "adf2md_version": self.adf2md_version,
            "profile_version": self.profile_version,
            "fallback_used": self.fallback_used,
            "warnings": list(self.warnings),
            "duration_ms": self.duration_ms,
            "raw_hash": self.raw_hash,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "unit_failures": [u.to_dict() for u in self.unit_failures],
            "scanned_page_fraction": self.scanned_page_fraction,
            "is_scanned_pdf": self.is_scanned_pdf,
            "front_matter": dict(self.front_matter) if self.front_matter is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExtractionRecord:
        """Rebuild from a stored ``extraction`` dict (legacy dicts may lack keys)."""
        failures = [
            UnitFailure(
                kind=str(u.get("kind", "")),
                ref=str(u.get("ref", "")),
                code=str(u.get("code", "")),
                message=str(u.get("message", "")),
            )
            for u in data.get("unit_failures") or []
            if isinstance(u, Mapping)
        ]
        raw_hash = data.get("raw_hash")
        return cls(
            engine=str(data.get("engine", "unknown")),
            engine_version=str(data.get("engine_version", "0")),
            raw_hash=str(raw_hash) if raw_hash else RAW_HASH_EMPTY,
            duration_ms=int(data.get("duration_ms") or 0),
            fallback_used=bool(data.get("fallback_used", False)),
            warnings=[str(w) for w in data.get("warnings") or []],
            html2md_version=data.get("html2md_version"),
            adf2md_version=data.get("adf2md_version"),
            profile_version=int(data.get("profile_version") or PROFILE_VERSION),
            failure_code=data.get("failure_code"),
            failure_message=data.get("failure_message"),
            unit_failures=failures,
            scanned_page_fraction=data.get("scanned_page_fraction"),
            is_scanned_pdf=bool(data.get("is_scanned_pdf", False)),
            front_matter=dict(data["front_matter"]) if data.get("front_matter") else None,
        )
