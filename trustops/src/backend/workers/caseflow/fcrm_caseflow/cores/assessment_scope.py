"""Which risk domains a case is assessed in, and which fields feed each one.

Pure functions over data S1 and S3 return; no I/O. Two facts drive the
assessment half:

* the **in-scope domains** (the ones the research and assessment agents are
  asked about and the ones S2 scores): S1's record names them once the case is
  submitted; when it does not, they follow from the confirmed change types
  (:data:`TYPE_DOMAINS`, a copy of the seed's ``type_domains`` that a lock test
  holds to the seed);
* the **input fields** of each domain: what an item in that domain depends on,
  so a re-run supersedes exactly the items whose inputs the owner changed. A
  domain depends on the always-required fields and on the fields of every change
  type that puts the case in that domain.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Final

from fcrm_contracts.api.case_internal import CaseRecordInternal
from fcrm_contracts.api.policy import FieldSetOut
from fcrm_contracts.enums import Domain

logger = logging.getLogger(__name__)

#: Change type -> the risk domains it puts a case in (seed ``type_domains``).
TYPE_DOMAINS: Final[Mapping[str, tuple[Domain, ...]]] = {
    "new_product": (Domain.PRODUCT,),
    "new_feature": (Domain.PRODUCT,),
    "process_change": (Domain.PRODUCT, Domain.CUSTOMER),
    "vendor_onboarding": (Domain.CHANNEL,),
    "new_geography": (Domain.GEOGRAPHY,),
    "new_segment": (Domain.CUSTOMER,),
}


def _sorted(domains: set[Domain]) -> list[Domain]:
    return sorted(domains, key=lambda domain: domain.value)


def domains_for_types(change_types: Sequence[str]) -> list[Domain]:
    """The domains ``change_types`` put a case in, sorted by name. An unknown
    change type adds none (and is logged: it cannot be a member of the enum S1
    validates, so this is a guard against a stale seed, not a normal path).
    """
    found: set[Domain] = set()
    for change_type in change_types:
        mapped = TYPE_DOMAINS.get(change_type)
        if mapped is None:
            logger.warning(
                "change type has no domain mapping", extra={"change_type": change_type}
            )
            continue
        found.update(mapped)
    return _sorted(found)


def domains_for(record: CaseRecordInternal) -> list[Domain]:
    """The domains the case is assessed in: those S1's record names, else those
    its change types imply.
    """
    if record.domains:
        named: set[Domain] = set()
        for name in record.domains:
            try:
                named.add(Domain(name))
            except ValueError:
                logger.warning("record names an unknown domain", extra={"domain": name})
        if named:
            return _sorted(named)
    return domains_for_types([t.value for t in record.change_types])


def input_fields_by_domain(
    field_set: FieldSetOut, change_types: Sequence[str]
) -> dict[Domain, list[str]]:
    """For each domain ``change_types`` put the case in, the field ids an item
    in it depends on: the always-required fields, then the fields of each change
    type that maps to the domain, in field-set order and de-duplicated.
    """
    always = [field.field_id for field in field_set.always_required]
    fields: dict[Domain, list[str]] = {}
    for change_type in change_types:
        own = [f.field_id for f in field_set.per_type.get(change_type, [])]
        for domain in TYPE_DOMAINS.get(change_type, ()):
            known = fields.setdefault(domain, list(always))
            known.extend(field_id for field_id in own if field_id not in known)
    return fields


def scope_refs(record: CaseRecordInternal) -> dict[str, Any]:
    """The run refs the engine hands every agent job it dispatches
    (:func:`fcrm_caseflow.dispatch.dispatch_refs`): the in-scope domains and the
    submission version the agents read.
    """
    refs: dict[str, Any] = {"domains": [d.value for d in domains_for(record)]}
    if record.submission_version is not None:
        refs["submission_version"] = record.submission_version
    return refs
