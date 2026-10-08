"""S1 counts ``fcrm_override_total{agent}`` when the analyst disagrees with an
agent's proposal on a review item (override, adjust or remove), once per
committed decision: a confirm is not counted, a replay is not counted again, and
a metric failure never changes the response.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.analyst_commands import AGENT_FOR_KIND, ALLOWED_DECISIONS
from fcrm_case.domain.views import ReviewItemRow
from fcrm_common.metrics import get_metrics
from fcrm_contracts.enums import AgentName, ItemKind
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
REASON = "Audit predates the corridor arrangement and covers domestic scope only."
OVERRIDE = {"decision": "override", "value": 4.5, "reason": REASON}


@pytest.fixture
def override_reader(review_service: CaseService) -> InMemoryMetricReader:
    """An in-memory reader behind the metrics the S1 app hands its analyst
    write service.
    """
    reader = InMemoryMetricReader()
    app = review_service.client.app
    app.state.metrics = get_metrics(MeterProvider(metric_readers=[reader]))  # type: ignore[attr-defined]
    return reader


def override_counts(reader: InMemoryMetricReader) -> dict[str, int]:
    data = reader.get_metrics_data()
    counts: dict[str, int] = {}
    if data is None:
        return counts
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != "fcrm_override_total":
                    continue
                for point in metric.data.data_points:
                    counts[str(point.attributes["agent"])] = int(point.value)
    return counts


def scored_t01(service: CaseService) -> ReviewItemRow:
    return next(
        r
        for r in service.views.items.values()
        if r.kind is ItemKind.CONTROL
        and r.subject.get("control_id") == "CTL-T01"
        and r.subject.get("planned") is False
    )


def post_item(service: CaseService, row: ReviewItemRow, body: Any) -> Any:
    return service.client.post(f"/cases/{CASE}/items/{row.item_id}", json=body)


def test_every_analyst_decided_kind_names_the_agent_that_proposed_it() -> None:
    assert set(AGENT_FOR_KIND) == set(ALLOWED_DECISIONS)
    assert AGENT_FOR_KIND[ItemKind.CLAUSE] is AgentName.RESEARCH
    assert AGENT_FOR_KIND[ItemKind.CONTROL] is AgentName.ASSESSMENT
    assert AGENT_FOR_KIND[ItemKind.FACTOR] is AgentName.ASSESSMENT
    assert AGENT_FOR_KIND[ItemKind.CRITIC_FINDING] is AgentName.CRITIC


def test_overriding_ctl_t01_counts_one_assessment_override(
    review_service: CaseService, override_reader: InMemoryMetricReader
) -> None:
    response = post_item(review_service, scored_t01(review_service), OVERRIDE)

    assert response.status_code == 200, response.text
    assert override_counts(override_reader) == {"assessment": 1}


def test_a_confirm_is_not_an_override(
    review_service: CaseService, override_reader: InMemoryMetricReader
) -> None:
    response = post_item(
        review_service, scored_t01(review_service), {"decision": "confirm"}
    )

    assert response.status_code == 200, response.text
    assert override_counts(override_reader) == {}


def test_a_replayed_override_is_counted_once(
    review_service: CaseService, override_reader: InMemoryMetricReader
) -> None:
    row = scored_t01(review_service)

    first = post_item(review_service, row, OVERRIDE)
    second = post_item(review_service, row, OVERRIDE)

    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert override_counts(override_reader) == {"assessment": 1}


def test_removing_a_clause_counts_a_research_override(
    review_service: CaseService,
    override_reader: InMemoryMetricReader,
    make_review_item: Callable[..., ReviewItemRow],
) -> None:
    clause = make_review_item(
        ItemKind.CLAUSE,
        {"clause_id": "AML-12", "domain": "channel", "corpus_v": 14},
        {"quote": "q", "applicability": "a", "confidence": 0.9},
    )
    review_service.views.items[clause.item_id] = clause

    response = post_item(
        review_service, clause, {"decision": "remove", "reason": REASON}
    )

    assert response.status_code == 200, response.text
    assert override_counts(override_reader) == {"research": 1}


class _BrokenCounter:
    """A counter whose ``add`` fails the way a bad attribute value would."""

    def add(self, amount: int, attributes: Any = None) -> None:
        raise ValueError("bad attribute")


def test_a_metric_failure_never_changes_the_response(
    review_service: CaseService,
) -> None:
    app = review_service.client.app
    app.state.metrics = dataclasses.replace(  # type: ignore[attr-defined]
        get_metrics(MeterProvider()), override_total=_BrokenCounter()
    )

    response = post_item(review_service, scored_t01(review_service), OVERRIDE)

    assert response.status_code == 200, response.text
    assert response.json()["replayed"] is False
