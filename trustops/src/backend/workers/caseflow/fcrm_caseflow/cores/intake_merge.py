"""Merging the intake agents' output (design B §2.2): pure functions, no I/O.

The intake agent runs once per document (the cheap budget cannot hold a whole
multi-page PDF), so one case yields several ``agent.done`` bodies whose
``accepted`` lists mix four proposal shapes. Code merges them before the PO
sees anything: values are deduped by ``field_id`` keeping the highest
confidence, change types by ``change_type``, gap questions by the field they
ask about, flags by their text. Nothing is generated here; every value, quote
and source reference is the agent's, copied.

Every tie is broken by a fixed key (confidence, then document, page, line,
then the text itself), so merging the same results in any order gives the same
proposals and the same ids: a step that runs twice pushes the same body.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.field_match import field_for_question
from fcrm_contracts.api.case_internal import (
    CategoryProposalItem,
    FactFlagItem,
    GapQuestionItem,
    ValueAlternative,
    ValueProposal,
)
from fcrm_contracts.enums import ChangeType, GateType
from fcrm_contracts.proposals.intake import (
    CategoryProposal,
    ExtractedField,
    ExtractionProposal,
    FactFlag,
    GapQuestion,
)
from fcrm_contracts.proposals.provenance import DocSourceRef
from pydantic import BaseModel, ValidationError

from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.store import AgentResult

logger = logging.getLogger(__name__)

#: The other values a field keeps for the owner to switch to (the rest are
#: still counted in ``candidates``).
MAX_ALTERNATIVES: Final = 5

#: Labels the PO sees for each change type (the walkthrough seed's
#: ``change_types``). Changing one is a UI change, not a policy change.
CHANGE_TYPE_LABELS: Final[Mapping[ChangeType, str]] = {
    ChangeType.NEW_PRODUCT: "New product",
    ChangeType.NEW_FEATURE: "New feature",
    ChangeType.PROCESS_CHANGE: "Process change",
    ChangeType.VENDOR_ONBOARDING: "Vendor / third-party onboarding",
    ChangeType.NEW_GEOGRAPHY: "New geography",
    ChangeType.NEW_SEGMENT: "New customer segment",
}


@dataclass(frozen=True)
class MergedIntake:
    """What the intake agents proposed, merged.

    Attributes:
        values: One per field, best candidate first by field id.
        candidates_found: Every candidate value the agents extracted, before
            deduping (the seed's "15 candidate values").
        categories: One per proposed change type, most confident first.
        gaps: One per field still to be asked about.
        flags: Facts the PO did not state that may extend or contradict a value.
        doc_ids: The documents the proposals cite.
        dropped_non_document_sources: Candidates and change types whose source
            is not a document span (they cannot show a page and line, so they
            are left out and counted, never shown).
    """

    values: tuple[ValueProposal, ...]
    candidates_found: int
    categories: tuple[CategoryProposalItem, ...]
    gaps: tuple[GapQuestionItem, ...]
    flags: tuple[FactFlagItem, ...]
    doc_ids: tuple[str, ...]
    dropped_non_document_sources: int


@dataclass(frozen=True)
class _Shards:
    """The accepted proposals of every result, by shape."""

    fields: tuple[ExtractedField, ...]
    categories: tuple[CategoryProposal, ...]
    gaps: tuple[GapQuestion, ...]
    flags: tuple[FactFlag, ...]


def _model_for(item: Mapping[str, Any]) -> type[BaseModel]:
    """Which proposal shape ``item`` is, from the keys that tell them apart.

    ``accepted`` carries no discriminator (H2 dumps each kept model as is), so
    the shape is read off the fields only one of them has.

    Raises:
        fcrm_common.errors.ValidationError: no shape matches.
    """
    if "window_id" in item and "fields" in item:
        return ExtractionProposal
    if "change_type" in item:
        return CategoryProposal
    if "question_id" in item:
        return GapQuestion
    if "text" in item and "quote" in item:
        return FactFlag
    raise FcrmValidationError(
        "an accepted intake proposal matches no known shape",
        keys=sorted(str(k) for k in item),
    )


def _parse(item: Mapping[str, Any], job_id: str) -> BaseModel:
    model = _model_for(item)
    try:
        return model.model_validate(item)
    except ValidationError as exc:
        raise FcrmValidationError(
            f"an accepted intake proposal is not a valid {model.__name__}",
            job_id=job_id,
            errors_count=exc.error_count(),
        ) from exc


def _shards(results: Sequence[AgentResult]) -> _Shards:
    fields: list[ExtractedField] = []
    categories: list[CategoryProposal] = []
    gaps: list[GapQuestion] = []
    flags: list[FactFlag] = []
    for result in results:
        for item in result.accepted():
            parsed = _parse(item, result.job_id)
            if isinstance(parsed, ExtractionProposal):
                fields.extend(parsed.fields)
            elif isinstance(parsed, CategoryProposal):
                categories.append(parsed)
            elif isinstance(parsed, GapQuestion):
                gaps.append(parsed)
            elif isinstance(parsed, FactFlag):
                flags.append(parsed)
    return _Shards(tuple(fields), tuple(categories), tuple(gaps), tuple(flags))


def _normalised(value: str) -> str:
    """``value`` with case and whitespace folded, for counting distinct candidates."""
    return " ".join(value.split()).casefold()


def _rank(source: DocSourceRef, confidence: float, text: str) -> tuple[Any, ...]:
    """The sort key that picks a winner: most confident, then the earliest
    place in the earliest document, then the text.
    """
    return (-confidence, source.doc_id, source.page, source.line_start, text)


def _doc_source(claim_source: object) -> Optional[DocSourceRef]:
    return claim_source if isinstance(claim_source, DocSourceRef) else None


def merge_values(fields: Sequence[ExtractedField]) -> tuple[list[ValueProposal], int]:
    """One :class:`ValueProposal` per ``field_id``: the most confident
    candidate; ``candidates`` counts the distinct values seen for the field.

    Returns:
        The proposals (by field id) and the number of candidates dropped for
        having no document source.
    """
    by_field: dict[str, list[tuple[ExtractedField, DocSourceRef]]] = {}
    dropped = 0
    for field in fields:
        source = _doc_source(field.source_ref)
        if source is None:
            dropped += 1
            continue
        by_field.setdefault(field.field_id, []).append((field, source))
    values: list[ValueProposal] = []
    for field_id in sorted(by_field):
        candidates = by_field[field_id]
        ranked = sorted(
            candidates, key=lambda c: _rank(c[1], c[0].confidence, c[0].value)
        )
        best, source = ranked[0]
        seen = {_normalised(best.value)}
        alternatives: list[ValueAlternative] = []
        for other, other_source in ranked[1:]:
            key = _normalised(other.value)
            if key in seen:
                continue
            seen.add(key)
            alternatives.append(
                ValueAlternative(value=other.value, source=other_source)
            )
        values.append(
            ValueProposal(
                field_id=field_id,
                label=best.label,
                value=best.value,
                source=source,
                quote=best.quote,
                confidence=best.confidence,
                candidates=len(seen),
                alternatives=alternatives[:MAX_ALTERNATIVES],
            )
        )
    return values, dropped


def merge_categories(
    proposals: Sequence[CategoryProposal], run_id: str
) -> tuple[list[CategoryProposalItem], int]:
    """One :class:`CategoryProposalItem` per change type, most confident
    first. Its ``gate_id`` is the id of the ``po_confirm_category`` gate the
    ``intake.publish`` step opens for it (:func:`deterministic_gate_id`).

    Returns:
        The items and the number dropped for having no document source.
    """
    by_type: dict[ChangeType, list[tuple[CategoryProposal, DocSourceRef]]] = {}
    dropped = 0
    for proposal in proposals:
        source = _doc_source(proposal.source_ref)
        if source is None:
            dropped += 1
            continue
        by_type.setdefault(proposal.change_type, []).append((proposal, source))
    items: list[CategoryProposalItem] = []
    for change_type, candidates in by_type.items():
        best, source = min(
            candidates, key=lambda c: _rank(c[1], c[0].confidence, c[0].quote)
        )
        items.append(
            CategoryProposalItem(
                change_type=change_type,
                label=CHANGE_TYPE_LABELS[change_type],
                source=source,
                quote=best.quote,
                confidence=best.confidence,
                gate_id=category_gate_id(run_id, change_type),
            )
        )
    items.sort(key=lambda i: (-i.confidence, i.change_type.value))
    return items, dropped


def category_gate_id(run_id: str, change_type: ChangeType) -> str:
    """The id of the ``po_confirm_category`` gate for ``change_type``."""
    return deterministic_gate_id(
        run_id, GateType.PO_CONFIRM_CATEGORY, change_type.value
    )


def gap_gate_id(run_id: str, question_id: str) -> str:
    """The id of the ``po_answer_gaps`` gate for ``question_id``."""
    return deterministic_gate_id(run_id, GateType.PO_ANSWER_GAPS, question_id)


def merge_gaps(
    questions: Sequence[GapQuestion],
    *,
    run_id: str,
    answered_fields: frozenset[str],
    labels: Mapping[str, str],
) -> list[GapQuestionItem]:
    """One :class:`GapQuestionItem` per field still to ask about.

    A question that names no field is tied to the field it asks about
    (:func:`fcrm_common.field_match.field_for_question` over ``labels``), so
    "Who is the change owner?" is the F01 question, not a second one (D-128).
    Questions about the same field collapse into one (blocking if any was; the
    smallest ``question_id`` wins so the choice does not depend on order). A
    question about a field some document did answer is dropped: the agent that
    read the other document could not know. Only a blocking question gets a
    gate (``gate_id``); a non-blocking one is shown, never required.

    Args:
        questions: Every ``GapQuestion`` from every shard.
        run_id: The run, for the gate ids.
        answered_fields: Fields the merged values already fill.
        labels: Field id -> label, for the PO's list.
    """
    by_key: dict[str, list[GapQuestion]] = {}
    for asked in questions:
        field_id = asked.field_id or field_for_question(asked.text, labels)
        if field_id in answered_fields:
            continue
        question = asked.model_copy(update={"field_id": field_id})
        key = field_id or f"q:{question.question_id}"
        by_key.setdefault(key, []).append(question)
    items: list[GapQuestionItem] = []
    for key in sorted(by_key):
        group = by_key[key]
        blocking = any(q.blocking for q in group)
        chosen = min(
            (q for q in group if q.blocking == blocking), key=lambda q: q.question_id
        )
        field_id = chosen.field_id
        items.append(
            GapQuestionItem(
                question_id=chosen.question_id,
                field_id=field_id,
                label=labels.get(field_id or "", field_id or "Question"),
                text=chosen.text,
                blocking=blocking,
                gate_id=gap_gate_id(run_id, chosen.question_id) if blocking else None,
            )
        )
    return items


def merge_flags(flags: Sequence[FactFlag]) -> list[FactFlagItem]:
    """Flags deduped by ``(field_id, kind, text)``, in a fixed order."""
    unique = {(f.field_id or "", f.kind, f.text): f for f in flags}
    return [
        FactFlagItem(field_id=f.field_id, text=f.text, kind=f.kind)
        for _, f in sorted(unique.items())
    ]


def merge_intake(
    results: Sequence[AgentResult],
    *,
    run_id: str,
    labels: Optional[Mapping[str, str]] = None,
) -> MergedIntake:
    """Merge every intake result of a run (see the module docstring).

    Args:
        results: The stored ``agent.done`` of every intake job, any order.
        run_id: The run, for the gate ids on categories and blocking gaps.
        labels: Field id -> label for the gap list; a value's own label (from
            the agent) is used for extracted fields.

    Raises:
        fcrm_common.errors.ValidationError: an accepted item has no known
            shape or is not valid for it.
    """
    shards = _shards(results)
    values, dropped_values = merge_values(shards.fields)
    categories, dropped_categories = merge_categories(shards.categories, run_id)
    # The field set's labels win over the agent's own: a question is tied to
    # its field by the label the field set gives it (D-128).
    known_labels = {**{v.field_id: v.label for v in values}, **(labels or {})}
    gaps = merge_gaps(
        shards.gaps,
        run_id=run_id,
        answered_fields=frozenset(v.field_id for v in values),
        labels=known_labels,
    )
    dropped = dropped_values + dropped_categories
    if dropped:
        logger.warning(
            "intake proposals without a document source were left out",
            extra={"dropped": dropped},
        )
    doc_ids = sorted(
        {v.source.doc_id for v in values} | {c.source.doc_id for c in categories}
    )
    return MergedIntake(
        values=tuple(values),
        candidates_found=len(shards.fields),
        categories=tuple(categories),
        gaps=tuple(gaps),
        flags=tuple(merge_flags(shards.flags)),
        doc_ids=tuple(doc_ids),
        dropped_non_document_sources=dropped,
    )
