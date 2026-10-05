"""The shape of ``clauses.json``: the 197-clause policy corpus (ADR-29).

The seed declares corpus v14 as nine sources and 197 clauses but carries only
four clause texts. ``gen_clauses`` writes the rest once, as clearly synthetic
text, into ``clauses.json`` next to ``seed.json``; this module is the schema the
generator writes and the loader validates, so a hand-edited or truncated file
fails loudly on load instead of reaching S3 and H4.

Every clause has a ``provenance`` that says what it is:

``seed_verbatim``
    One of the four texts the walkthrough seed already carries (``demo.clauses``),
    kept character for character.
``synthetic_internal_standard``
    A clause of one of the four invented internal standards (Policy 4.2, Standard
    7.1, 2.3, 1.8), written by a model for this demonstration.
``synthetic_public_stand_in``
    A stand-in for a public source (FFIEC, OFAC, FinCEN, OCC, FATF). It names the
    kind of expectation the source covers and never quotes, paraphrases or cites a
    real publication, so policy "quoted verbatim with a source reference" stays
    honest: the reference points at a clause that says it is synthetic.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RISK_AREAS: Final[tuple[str, ...]] = ("customer", "product", "geography", "channel")
"""``fcrm_contracts.enums.Domain`` values (a test keeps the two in step)."""

TRIGGER_TYPES: Final[tuple[str, ...]] = (
    "new_product",
    "new_feature",
    "process_change",
    "vendor_onboarding",
    "new_geography",
    "new_segment",
)
"""``fcrm_contracts.enums.ChangeType`` values (a test keeps the two in step)."""

RiskArea = Literal["customer", "product", "geography", "channel"]
TriggerType = Literal[
    "new_product",
    "new_feature",
    "process_change",
    "vendor_onboarding",
    "new_geography",
    "new_segment",
]
ProvenanceKind = Literal[
    "seed_verbatim", "synthetic_internal_standard", "synthetic_public_stand_in"
]

JURISDICTION_PATTERN: Final = r"^(?:[A-Z]{2}|GLOBAL)$"
"""An ISO 3166 alpha-2 code, or ``GLOBAL`` for a clause that applies anywhere."""


_JURISDICTION = re.compile(JURISDICTION_PATTERN)


def _check_jurisdictions(values: list[str]) -> list[str]:
    bad = [v for v in values if not _JURISDICTION.fullmatch(v)]
    if bad:
        raise ValueError(f"jurisdictions must be ISO alpha-2 or GLOBAL: {bad}")
    return values


class ClauseProvenance(BaseModel):
    """Where a clause's text came from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ProvenanceKind
    generated_by: str = Field(min_length=1)
    generated_at: str = Field(min_length=10)
    note: str = Field(min_length=20)


class ClauseRecord(BaseModel):
    """One clause of corpus v14, with its code-assigned position."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_id: str = Field(min_length=3)
    source_id: str = Field(min_length=2)
    title: str = Field(min_length=3)
    text: str = Field(min_length=20)
    ref: str = Field(min_length=3)
    page: int = Field(ge=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    risk_areas: list[RiskArea] = Field(min_length=1)
    trigger_types: list[TriggerType] = Field(min_length=1)
    jurisdictions: list[str] = Field(min_length=1)
    quotable: bool
    provenance: ClauseProvenance

    @field_validator("jurisdictions")
    @classmethod
    def _jurisdictions_are_codes(cls, values: list[str]) -> list[str]:
        return _check_jurisdictions(values)


class ClausesMeta(BaseModel):
    """What produced the file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_version: str
    clause_count: int = Field(ge=1)
    generated_at: str
    generated_by: list[str] = Field(min_length=1)
    prompt_version: str
    prompt_sha256: str = Field(min_length=64, max_length=64)
    note: str


class ClausesFile(BaseModel):
    """``clauses.json`` as a whole."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    meta: ClausesMeta
    clauses: list[ClauseRecord]

    @field_validator("clauses")
    @classmethod
    def _ids_are_unique(cls, clauses: list[ClauseRecord]) -> list[ClauseRecord]:
        ids = [c.clause_id for c in clauses]
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        if duplicated:
            raise ValueError(f"duplicate clause ids: {duplicated}")
        return clauses

    @model_validator(mode="after")
    def _count_matches_the_meta(self) -> ClausesFile:
        if len(self.clauses) != self.meta.clause_count:
            raise ValueError(
                f"meta declares {self.meta.clause_count} clauses, "
                f"file has {len(self.clauses)} (truncated?)"
            )
        return self
