"""BB-02, "PO sees no scores" (``docs/architecture/design/
C-web-tests-ops-sdlc.md`` section 3.1(f): "Walk every PO endpoint for the
demo case and dashboard; JSON contains no key or string matching the
restricted regex; PO SSE stream for 30 s contains no ``score.*``/
``harness.*``/``audit.*`` events; PO token -> ``/monitor`` stream 403").

Three halves, three places:

* **The contract** (this file): ``fcrm_guardrails.rules.r3_po_no_restricted``
  is the real rule-3 scanner, and ``fcrm_contracts.api.case_po`` is every shape
  a PO response is built from. Every model in it is checked for restricted field
  names, and the models the demo case fills are built from the real seed and
  scanned as data.
* **The HTTP surface**: ``tests/unit/services/case/test_po_scenes_1_to_7.py``
  scans every response of the owner's journey, and
  ``test_po_routes.py::test_no_po_response_carries_a_restricted_key`` every
  route; ``tests/integration/cross_package/test_case_po_http_restricted.py`` adds
  the stream frames.
* **The fence itself**: ``test_po_guard.py`` injects a handler that leaks and
  sees the 500.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fcrm_contracts.api import case_po
from fcrm_contracts.api.case_po import (
    ConditionPO,
    GapQuestionPO,
    ReuseFieldPO,
    SourceRef,
    SubmitCheck,
    SubmitResult,
    ValuePO,
)
from fcrm_contracts.enums import Role
from fcrm_guardrails.restricted_terms import RESTRICTED_KEYS
from fcrm_guardrails.rules.r3_po_no_restricted import check
from pydantic import BaseModel

pytestmark = pytest.mark.integration


def po_models() -> list[type[BaseModel]]:
    """Every Pydantic model ``case_po`` defines."""
    return [
        model
        for _, model in inspect.getmembers(case_po, inspect.isclass)
        if issubclass(model, BaseModel)
        and model is not BaseModel
        and model.__module__ == case_po.__name__
    ]


def test_there_are_po_models_to_check() -> None:
    names = {m.__name__ for m in po_models()}
    assert {"CasePO", "IntakeViewPO", "ReviewViewPO", "OutcomePO"} <= names
    assert len(names) > 30


@pytest.mark.parametrize("model", po_models(), ids=lambda m: m.__name__)
def test_no_po_model_has_a_restricted_field_name(model: type[BaseModel]) -> None:
    names = {info.alias or name for name, info in model.model_fields.items()}
    result = check(dict.fromkeys(names, ""), Role.PO)
    assert result.ok, f"{model.__name__}: {result.detail}"
    assert names.isdisjoint(RESTRICTED_KEYS)


def test_the_models_the_demo_fills_are_clean_as_data(demo: dict[str, Any]) -> None:
    """The demo case's real field values, sources, conditions and the answers it
    gives, built into the PO shapes and scanned as the wire would carry them.
    """
    values = [
        ValuePO(
            field_id=f["id"],
            label=f["id"],
            value=f["value"],
            origin=f["how"] if f["how"] != "open" else "open",
            source=SourceRef(label=f.get("source") or "owner answer"),
            candidates=f.get("candidates"),
            alternatives=[],
            always_required=f["id"] in {"F01", "F02", "F03", "F04", "F05", "F06"},
        )
        for f in demo["fields"]
    ]
    conditions = [
        ConditionPO(
            id=c["id"],
            text=c["text"],
            owner=c["owner"],
            due=c["due"],
            status="open",
            mine=False,
        )
        for c in demo["conditions"]
    ]
    questions = [
        GapQuestionPO(
            id=f"q{n}",
            field_id=q.get("field_id", "F10"),
            label="x",
            question=q["q"],
            blocking=True,
            answered=False,
            value=None,
        )
        for n, q in enumerate(demo["questions"])
    ]
    reuse = [
        ReuseFieldPO(
            field_id="F31", label="Limits", value="$2,999", reusable=True, reason=None
        )
    ]
    result = SubmitResult(
        ok=True,
        checks=[
            SubmitCheck(
                id="completion",
                label="Completion",
                ok=True,
                blocking=True,
                detail="83% ≥ 80%",
            )
        ],
        submission_version=1,
        message="Submitted.",
    )
    for name, payload in (
        ("values", values),
        ("conditions", conditions),
        ("questions", questions),
        ("reuse", reuse),
        ("result", result),
    ):
        verdict = check(payload, Role.PO)
        assert verdict.ok, f"{name}: {verdict.detail}"
