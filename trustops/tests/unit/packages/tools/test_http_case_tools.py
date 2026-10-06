"""``evidence_store``, ``submission_diff`` and ``case_search`` over scripted S1
and S3 answers.

The evidence tool reads what S1 serves (``EvidenceStoreOutput``: artefact, date,
document), read tolerantly as ``CaseEvidenceOut``; ``case_search`` reads the
tool's own ``CaseSearchOutput`` and ``submission_diff`` a route S1 already serves.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_contracts.tools.case_search import CaseSearchInput
from fcrm_contracts.tools.evidence_store import (
    EvidenceItem,
    EvidenceStoreInput,
    EvidenceStoreOutput,
)
from fcrm_contracts.tools.submission_diff import SubmissionDiffInput
from fcrm_tools import (
    ToolInputError,
    ToolScopeError,
    UnknownEntityError,
    build_http_registry,
)

CASE_HOST = "case.test"
POLICY_HOST = "policy.test"
DEMO_CASE = "FCRM-2026-0151"
EVIDENCE_PATH = f"/internal/cases/{DEMO_CASE}/evidence"
DIFF_PATH = f"/internal/cases/{DEMO_CASE}/submissions/diff"
SEARCH_PATH = "/internal/cases/search"
CONTROLS_PATH = "/controls"


@pytest.fixture
def registry(tool_clients: Any, object_store: Any) -> Any:
    return build_http_registry(tool_clients, object_store)


def _controls() -> dict[str, Any]:
    return {
        "version": "v9",
        "active": True,
        "controls": [
            {
                "control_id": "CTL-T01",
                "domain": "channel",
                "name": "Vendor due diligence",
                "catalogue_weight": 0.35,
                "evidence_artifact": "DD report",
                "last_tested": "2026-03-01",
            }
        ],
    }


def _evidence(control_id: str = "CTL-T01", **item: Any) -> dict[str, Any]:
    """S1's answer in the shape it serves (``EvidenceStoreOutput``)."""
    first: dict[str, Any] = {
        "artifact": "Vendor due-diligence scope letter",
        "evidence_date": "",
        "doc_id": "doc_3",
    }
    first.update(item)
    return {"control_id": control_id, "items": [first]}


# ── evidence_store ─────────────────────────────────────────────────────────


@pytest.fixture
def evidence_peers(peers: Any) -> Any:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _controls())
    peers.respond(CASE_HOST, "GET", EVIDENCE_PATH, _evidence())
    return peers


def _ask(control_id: str = "CTL-T01") -> EvidenceStoreInput:
    return EvidenceStoreInput(case_id=DEMO_CASE, control_id=control_id)


async def test_evidence_store_describes_the_artefact_and_dates_it_by_the_last_test(
    registry: Any, evidence_peers: Any
) -> None:
    out = await registry.get("evidence_store").call(_ask())

    assert out.control_id == "CTL-T01"
    [item] = out.items
    assert item.artifact == "Vendor due-diligence scope letter"
    assert item.evidence_date == "2026-03-01"  # S1 gave none: the control's last test
    assert item.doc_id == "doc_3"
    assert dict(evidence_peers.calls(CASE_HOST, EVIDENCE_PATH)[0].url.params) == {
        "control_id": "CTL-T01"
    }


async def test_evidence_store_reads_exactly_what_s1_serves(
    registry: Any, peers: Any
) -> None:
    """S1's route answers its ``EvidenceStoreOutput`` model; the tool must read
    that shape, not an invented one (the first live run failed here).
    """
    served = EvidenceStoreOutput(
        control_id="CTL-T01",
        items=[EvidenceItem(artifact="DD report", evidence_date="", doc_id=None)],
    )
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _controls())
    peers.respond(CASE_HOST, "GET", EVIDENCE_PATH, served.model_dump(mode="json"))

    out = await registry.get("evidence_store").call(_ask())

    assert [(i.artifact, i.evidence_date) for i in out.items] == [
        ("DD report", "2026-03-01")
    ]


async def test_evidence_store_prefers_the_date_and_doc_s1_gives(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _controls())
    peers.respond(
        CASE_HOST,
        "GET",
        EVIDENCE_PATH,
        _evidence(evidence_date="2026-08-15", doc_id="doc_9"),
    )

    out = await registry.get("evidence_store").call(_ask())

    assert (out.items[0].evidence_date, out.items[0].doc_id) == ("2026-08-15", "doc_9")


async def test_a_case_with_no_evidence_of_its_own_gets_the_control_librarys(
    registry: Any, peers: Any
) -> None:
    """The first assessment of a new case: S1 holds no evidence yet, the control
    library does (the artefact and the date it was last tested). Reporting none
    made the assessment agent rate every control "no evidence seen".
    """
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _controls())
    peers.respond(
        CASE_HOST, "GET", EVIDENCE_PATH, {"control_id": "CTL-T01", "items": []}
    )
    out = await registry.get("evidence_store").call(_ask())
    assert [(i.artifact, i.evidence_date, i.doc_id) for i in out.items] == [
        ("DD report", "2026-03-01", None)
    ]


async def test_a_control_with_no_library_artefact_has_no_evidence_to_report(
    registry: Any, peers: Any
) -> None:
    controls = _controls()
    controls["controls"][0]["evidence_artifact"] = ""
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, controls)
    peers.respond(
        CASE_HOST, "GET", EVIDENCE_PATH, {"control_id": "CTL-T01", "items": []}
    )
    out = await registry.get("evidence_store").call(_ask())
    assert out.items == []


async def test_evidence_store_ignores_what_s1_adds_later(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _controls())
    body = _evidence(attested_by="u-riyer")
    body["generated_at"] = "2026-10-05"
    peers.respond(CASE_HOST, "GET", EVIDENCE_PATH, body)
    out = await registry.get("evidence_store").call(_ask())
    assert len(out.items) == 1


async def test_a_control_not_in_the_inventory_is_unknown_and_s1_is_never_asked(
    registry: Any, evidence_peers: Any
) -> None:
    with pytest.raises(UnknownEntityError, match="CTL-X99"):
        await registry.get("evidence_store").call(_ask("CTL-X99"))
    assert evidence_peers.calls(CASE_HOST, EVIDENCE_PATH) == []


async def test_evidence_store_unknown_case_is_unknown_entity(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _controls())
    with pytest.raises(UnknownEntityError, match="FCRM-2026-0151"):
        await registry.get("evidence_store").call(_ask())


async def test_evidence_store_refuses_an_answer_about_another_control(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _controls())
    peers.respond(CASE_HOST, "GET", EVIDENCE_PATH, _evidence(control_id="CTL-T02"))
    with pytest.raises(ToolScopeError, match="CTL-T02"):
        await registry.get("evidence_store").call(_ask())


async def test_evidence_store_refuses_a_malformed_answer(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _controls())
    peers.respond(CASE_HOST, "GET", EVIDENCE_PATH, {"control_id": "CTL-T01"})
    with pytest.raises(UpstreamUnavailableError, match="CaseEvidenceOut"):
        await registry.get("evidence_store").call(_ask())


async def test_the_catalogue_is_cached_but_the_evidence_never_is(
    registry: Any, evidence_peers: Any
) -> None:
    tool = registry.get("evidence_store")
    await tool.call(_ask())
    await tool.call(_ask())
    assert len(evidence_peers.calls(POLICY_HOST, CONTROLS_PATH)) == 1
    assert len(evidence_peers.calls(CASE_HOST, EVIDENCE_PATH)) == 2


async def test_a_case_id_cannot_add_a_path_segment(registry: Any, peers: Any) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _controls())
    with pytest.raises(UnknownEntityError):
        await registry.get("evidence_store").call(
            EvidenceStoreInput(case_id="../../admin", control_id="CTL-T01")
        )
    sent = peers.requests[-1].url.raw_path
    assert sent == b"/internal/cases/..%2F..%2Fadmin/evidence?control_id=CTL-T01"


# ── submission_diff ────────────────────────────────────────────────────────


async def test_submission_diff_returns_the_changed_fields(
    registry: Any, peers: Any
) -> None:
    peers.respond(
        CASE_HOST,
        "GET",
        DIFF_PATH,
        {
            "changed_fields": [
                {"field_id": "F03", "before": "Q1 2027", "after": "February 2027"},
                {"field_id": "F11", "before": None, "after": "Letter attached"},
            ]
        },
    )

    out = await registry.get("submission_diff").call(
        SubmissionDiffInput(case_id=DEMO_CASE, from_version=1, to_version=2)
    )

    assert [(f.field_id, f.before, f.after) for f in out.changed_fields] == [
        ("F03", "Q1 2027", "February 2027"),
        ("F11", None, "Letter attached"),
    ]
    assert dict(peers.requests[-1].url.params) == {
        "from_version": "1",
        "to_version": "2",
    }


async def test_submission_diff_between_identical_versions_is_empty(
    registry: Any, peers: Any
) -> None:
    peers.respond(CASE_HOST, "GET", DIFF_PATH, {"changed_fields": []})
    out = await registry.get("submission_diff").call(
        SubmissionDiffInput(case_id=DEMO_CASE, from_version=2, to_version=2)
    )
    assert out.changed_fields == []


@pytest.mark.parametrize(("from_v", "to_v"), [(0, 1), (1, 0), (-1, 2)])
async def test_submission_diff_refuses_a_version_below_one(
    registry: Any, peers: Any, from_v: int, to_v: int
) -> None:
    with pytest.raises(ToolInputError, match="start at 1"):
        await registry.get("submission_diff").call(
            SubmissionDiffInput(case_id=DEMO_CASE, from_version=from_v, to_version=to_v)
        )
    assert peers.requests == []


async def test_submission_diff_of_a_missing_version_is_unknown_entity(
    registry: Any,
) -> None:
    with pytest.raises(UnknownEntityError, match="versions 1 and 9"):
        await registry.get("submission_diff").call(
            SubmissionDiffInput(case_id=DEMO_CASE, from_version=1, to_version=9)
        )


# ── case_search ────────────────────────────────────────────────────────────


def _hits(count: int = 2) -> dict[str, Any]:
    return {
        "hits": [
            {
                "case_id": f"FCRM-2026-01{i:02d}",
                "title": f"Case {i}",
                "similarity": 0.9 - i / 10,
                "outcome": "approved" if i % 2 else None,
            }
            for i in range(count)
        ]
    }


async def test_case_search_sends_the_query_and_its_narrowing_to_s1(
    registry: Any, peers: Any
) -> None:
    peers.respond(CASE_HOST, "GET", SEARCH_PATH, _hits())

    out = await registry.get("case_search").call(
        CaseSearchInput(
            query="payout partner",
            change_types=["vendor_onboarding", "new_geography"],
            top_k=3,
            closed_only=False,
            geography="Mexico",
        )
    )

    assert [h.case_id for h in out.hits] == ["FCRM-2026-0100", "FCRM-2026-0101"]
    assert dict(peers.requests[-1].url.params) == {
        "q": "payout partner",
        "top_k": "3",
        "closed_only": "false",
        "change_types": "vendor_onboarding,new_geography",
        "geography": "Mexico",
    }


async def test_case_search_defaults_to_closed_cases_and_sends_no_empty_filter(
    registry: Any, peers: Any
) -> None:
    peers.respond(CASE_HOST, "GET", SEARCH_PATH, _hits())
    await registry.get("case_search").call(CaseSearchInput(query="remittance"))
    assert dict(peers.requests[-1].url.params) == {
        "q": "remittance",
        "top_k": "5",
        "closed_only": "true",
    }


async def test_case_search_trims_a_peer_that_returns_more_than_asked(
    registry: Any, peers: Any
) -> None:
    peers.respond(CASE_HOST, "GET", SEARCH_PATH, _hits(6))
    out = await registry.get("case_search").call(
        CaseSearchInput(query="remittance", top_k=2)
    )
    assert [h.case_id for h in out.hits] == ["FCRM-2026-0100", "FCRM-2026-0101"]


@pytest.mark.parametrize("query", ["", "   ", "x" * 1001])
async def test_case_search_refuses_an_empty_or_over_long_query(
    registry: Any, peers: Any, query: str
) -> None:
    with pytest.raises(ToolInputError, match="characters"):
        await registry.get("case_search").call(CaseSearchInput(query=query))
    assert peers.requests == []


@pytest.mark.parametrize("top_k", [0, 26])
async def test_case_search_refuses_a_top_k_out_of_range(
    registry: Any, top_k: int
) -> None:
    with pytest.raises(ToolInputError, match="top_k"):
        await registry.get("case_search").call(
            CaseSearchInput(query="remittance", top_k=top_k)
        )


async def test_case_search_refuses_a_malformed_answer(
    registry: Any, peers: Any
) -> None:
    peers.respond(CASE_HOST, "GET", SEARCH_PATH, {"hits": [{"case_id": "x"}]})
    with pytest.raises(UpstreamUnavailableError, match="CaseSearchOutput"):
        await registry.get("case_search").call(CaseSearchInput(query="remittance"))
