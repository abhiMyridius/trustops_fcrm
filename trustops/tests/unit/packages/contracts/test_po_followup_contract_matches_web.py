"""Drift lock: ``fcrm_contracts.api.case_po_followup`` accepts what the web's PO
screens post (ADR-30), read from the TypeScript, not a copy of it.

``useAnswerThread`` posts ``{ text, documents, field_values }`` (the thread id is in
the path; ``field_values`` since ADR-73, so the owner can set a field's new value
with the answer) and ``useConditionEvidence`` posts ``{ note }``. The Python bodies
must take every key the web sends and nothing it does not. The response models are
the PO models already locked against ``po.ts`` by
``test_po_contract_matches_web_types``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fcrm_contracts.api import case_po_followup as followup

QUERIES = (
    Path(__file__).resolve().parents[4] / "src/frontend/src/api/queries/caseQueries.ts"
)

#: hook -> (model, keys the model takes that the web does not send yet)
HOOKS = {
    "useAnswerThread": ("ThreadAnswerBody", frozenset()),
    "useConditionEvidence": ("ConditionEvidenceBody", frozenset()),
}


def hook_source(name: str) -> str:
    source = QUERIES.read_text(encoding="utf-8")
    match = re.search(rf"export function {name}\(.*?\n}}\n", source, flags=re.S)
    assert match, f"{name} not found in caseQueries.ts"
    return match.group(0)


def posted_keys(hook: str) -> set[str]:
    """The keys of the ``body: { ... }`` literal the hook sends."""
    source = hook_source(hook)
    match = re.search(r"body:\s*\{([^}]*)\}", source)
    assert match, f"{hook} posts no body object"
    return {
        found.group(1)
        for part in match.group(1).split(",")
        if (found := re.match(r"\s*(\w+)\s*(?::|$)", part))
    }


@pytest.mark.parametrize("hook", sorted(HOOKS))
def test_every_key_the_web_posts_is_a_field_of_the_body(hook: str) -> None:
    model = getattr(followup, HOOKS[hook][0])

    assert posted_keys(hook) - set(model.model_fields) == set()


@pytest.mark.parametrize("hook", sorted(HOOKS))
def test_the_body_takes_nothing_the_web_does_not_send_but_its_extension(
    hook: str,
) -> None:
    name, extension = HOOKS[hook]
    model = getattr(followup, name)

    assert set(model.model_fields) - posted_keys(hook) <= extension


def test_the_checker_is_not_vacuous() -> None:
    assert posted_keys("useAnswerThread") == {"text", "documents", "field_values"}
    assert posted_keys("useConditionEvidence") == {"note"}
