"""The pure rules behind the product owner's writes: which change types a case
holds once the owner confirms or removes one, and what that does to the fields
the case must fill and the risk domains in scope. No I/O: both case stores call
:func:`apply_type_delta` inside their own transaction, so the Mongo and the
in-memory twin cannot drift apart.

A confirmed type is the only thing that changes the required field set (the
union of the always-required fields and each confirmed type's own fields,
``fcrm_case.domain.fieldset.resolve_required_fields``); a type that is merely
proposed changes nothing on the case document.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fcrm_contracts.enums import ChangeType

from fcrm_case.domain.fieldset import FieldSetData, resolve_required_fields
from fcrm_case.domain.record import CaseRecord


def domains_for(
    change_types: Sequence[ChangeType], type_domains: Mapping[str, Sequence[str]]
) -> list[str]:
    """The risk domains in scope for ``change_types``: the union of each type's
    domains, alphabetical so the list is stable (``channel``, ``geography``,
    ``product`` for the demo case).
    """
    in_scope = {d for t in change_types for d in type_domains.get(t.value, ())}
    return sorted(in_scope)


def apply_type_delta(
    record: CaseRecord,
    change_type: ChangeType,
    *,
    confirmed: bool,
    field_set: FieldSetData,
    type_domains: Mapping[str, Sequence[str]],
) -> CaseRecord:
    """``record`` after the owner confirmed (``confirmed=True``) or removed
    ``change_type``: the type joins ``change_types`` (in confirmation order) or
    leaves it, and the required fields and domains follow. Confirming a type
    twice, or removing one that was never confirmed, changes nothing.
    """
    types = [t for t in record.change_types if t != change_type]
    if confirmed:
        types = [*record.change_types, change_type]
        if change_type in record.change_types:
            types = list(record.change_types)
    return record.model_copy(
        update={
            "change_types": types,
            "required_fields": resolve_required_fields(
                [t.value for t in types], field_set
            ),
            "domains": domains_for(types, type_domains),
        }
    )
