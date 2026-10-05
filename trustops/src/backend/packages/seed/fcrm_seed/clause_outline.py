"""The outline of corpus v14: how each source's clauses are numbered (ADR-29).

Data for :mod:`fcrm_seed.gen_clauses`. The seed declares nine sources, their
section names and a clause count each; it does not say how the clauses of a
source divide among its sections or what their ids are. This module does, and
:func:`fcrm_seed.gen_clauses.build_plan` checks every source's section labels and
total against the seed, so the two cannot drift.

Ids are stable and carry their place: ``FFIEC-TPPP-04`` is the fourth clause of
the Third-Party Payment Processors section, ``TP71-2.3`` the third clause of
section 2 of Standard 7.1. The four ids the seed already uses
(``FFIEC-TPPP-04``, ``FCRM42-3.1``, ``TP71-2.3``, ``OFAC-1.2``) fall out of the
scheme, so the seed's verbatim texts keep their ids.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class SectionPlan:
    """One section of a source: its seed label, its id code and its size."""

    code: str
    label: str
    count: int


@dataclass(frozen=True)
class SourceOutline:
    """How a source's clauses are numbered and referenced."""

    prefix: str
    sections: tuple[SectionPlan, ...]
    ref_template: str
    coded: bool = False


def _numbered(
    prefix: str, ref_template: str, sections: Sequence[tuple[str, int]]
) -> SourceOutline:
    return SourceOutline(
        prefix,
        tuple(
            SectionPlan(str(i), label, count)
            for i, (label, count) in enumerate(sections, start=1)
        ),
        ref_template,
    )


#: Per-source outline. Section labels and the total per source must equal the
#: seed's own ``corpus.sources`` (checked by :func:`build_plan`); the split of
#: clauses across a source's sections is this tool's choice.
OUTLINE: Final[Mapping[str, SourceOutline]] = {
    "FFIEC": SourceOutline(
        "FFIEC",
        (
            SectionPlan("BRA", "BSA/AML Risk Assessment", 14),
            SectionPlan("CDD", "Customer Due Diligence", 16),
            SectionPlan("TPPP", "Third-Party Payment Processors", 14),
            SectionPlan("EB", "Electronic Banking", 10),
            SectionPlan("FT", "Funds Transfers", 12),
            SectionPlan("MSB", "Nonbank Financial Institutions / MSBs", 12),
            SectionPlan("FCA", "Foreign Correspondent Accounts (summary)", 8),
        ),
        "FFIEC synthetic stand-in · {label} · item {ordinal:02d}",
        coded=True,
    ),
    "OFAC": _numbered(
        "OFAC",
        "OFAC synthetic stand-in · {label} · item {section}.{ordinal}",
        [
            ("Screening obligations", 6),
            ("List types (SDN, sectoral)", 4),
            ("50% rule", 4),
        ],
    ),
    "FINCEN-2026": _numbered(
        "FINCEN",
        "FINCEN-2026 synthetic stand-in · {label} · item {section}.{ordinal}",
        [
            ("Risk assessment process", 5),
            ("Update on new products, markets, customer types", 4),
        ],
    ),
    "OCC-2017-43": _numbered(
        "OCC43",
        "OCC-2017-43 synthetic stand-in · {label} · item {section}.{ordinal}",
        [("Due diligence", 4), ("Approval", 3), ("Post-launch monitoring", 4)],
    ),
    "FATF": _numbered(
        "FATF",
        "FATF synthetic stand-in · {label} · item {section}.{ordinal}",
        [("Call for action", 1), ("Increased monitoring", 1)],
    ),
    "INT-FCRM-4.2": _numbered(
        "FCRM42",
        "Policy 4.2 §{section}.{ordinal} · v2026-03",
        [
            ("§1 Scope", 3),
            ("§2 Jurisdiction rating", 5),
            ("§3 Screening and EDD", 6),
            ("§4 Reporting", 4),
        ],
    ),
    "INT-TP-7.1": _numbered(
        "TP71",
        "Standard 7.1 §{section}.{ordinal} · v2025-11",
        [
            ("§1 Scope", 3),
            ("§2 Payout agents", 7),
            ("§3 Agent KYC standard", 6),
            ("§4 Contract clauses", 6),
        ],
    ),
    "INT-PR-2.3": _numbered(
        "PR23",
        "Standard 2.3 §{section}.{ordinal} · v2026-01",
        [("§1 Sign-off", 5), ("§2 Limits and velocity", 5), ("§3 Disclosure", 5)],
    ),
    "INT-CO-1.8": _numbered(
        "CO18",
        "Standard 1.8 §{section}.{ordinal} · v2026-02",
        [
            ("§1 KYC/CDD", 6),
            ("§2 PEP and BO", 5),
            ("§3 Periodic refresh", 4),
            ("§4 Segment attestations", 5),
        ],
    ),
}

#: Tags for the four verbatim clauses (the text is the seed's; code tags it).
VERBATIM_TAGS: Final[Mapping[str, tuple[list[str], list[str], list[str]]]] = {
    "FFIEC-TPPP-04": (
        ["channel", "product"],
        ["vendor_onboarding", "new_product"],
        ["US"],
    ),
    "FCRM42-3.1": (["geography"], ["new_geography"], ["GLOBAL"]),
    "TP71-2.3": (["channel"], ["vendor_onboarding"], ["GLOBAL"]),
    "OFAC-1.2": (
        ["geography", "channel"],
        ["new_geography", "vendor_onboarding", "new_product"],
        ["US"],
    ),
}


def clause_id_for(outline: SourceOutline, section: SectionPlan, ordinal: int) -> str:
    """The stable id of the ``ordinal``-th clause of ``section``."""
    if outline.coded:
        return f"{outline.prefix}-{section.code}-{ordinal:02d}"
    return f"{outline.prefix}-{section.code}.{ordinal}"
