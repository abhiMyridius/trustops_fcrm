"""The configuration routes over HTTP: who may call them, the web's request and
response shapes, the status codes of every refusal and the whole
edit-preview-publish flow.

Authorization is checked twice, as in production: ``require_roles`` runs for
real against the principal ``act_as`` sets while OPA is an allow-all stub, so a
403 here is the route's own refusal (the OPA data is tested by ``opa test``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from fcrm_policy.main import create_app
from fcrm_policy.reference import InMemoryReferenceStore

NOTE = "Rebalanced after the 2026 annual review of the model"
SECTION_KEYS = [
    "field-sets",
    "categories",
    "scoring-model",
    "control-inventory",
    "policy-corpus",
    "routing-rules",
    "jurisdiction-risk",
    "ai-agents",
]
DRAFT_BODY = {"path": "max_required", "before": "18", "after": "12"}
Caller = Callable[[Role], TestClient]


def analyst(config_as: Caller) -> TestClient:
    return config_as(Role.ANALYST)


# ── who may call ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/config/field-sets", None),
        ("PUT", "/config/field-sets/draft", DRAFT_BODY),
        ("DELETE", "/config/field-sets/draft", None),
        ("POST", "/config/field-sets/draft/preview", {}),
        ("POST", "/config/field-sets/draft/publish", {"note": NOTE}),
    ],
)
@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_the_analyst_may_use_a_configuration_route(
    config_as: Caller, role: Role, method: str, path: str, body: Any
) -> None:
    response = config_as(role).request(method, path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("/forbidden")


def test_a_denied_publish_changes_nothing(config_as: Caller) -> None:
    client = analyst(config_as)
    client.put("/config/field-sets/draft", json=DRAFT_BODY)
    config_as(Role.SERVICE).post(
        "/config/field-sets/draft/publish", json={"note": NOTE}
    )
    again = analyst(config_as).get("/config/field-sets").json()
    assert again["active_version"] == "v6"
    assert len(again["draft"]["changes"]) == 1


def test_the_configuration_routes_need_a_token() -> None:
    client = TestClient(create_app())
    assert client.get("/config/field-sets").status_code == 401
    assert client.put("/config/field-sets/draft", json=DRAFT_BODY).status_code == 401
    assert client.delete("/config/field-sets/draft").status_code == 401
    assert client.post("/config/field-sets/draft/preview").status_code == 401
    assert (
        client.post("/config/field-sets/draft/publish", json={"note": NOTE}).status_code
        == 401
    )


# ── reading ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", SECTION_KEYS)
def test_every_section_is_served_in_the_webs_shape(config_as: Caller, key: str) -> None:
    response = analyst(config_as).get(f"/config/{key}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "section",
        "title",
        "owner",
        "active_version",
        "versions",
        "draft",
        "data",
    }
    assert body["section"] == key
    assert set(body["draft"]) == {"etag", "changes"}
    assert set(body["versions"][0]) == {
        "version",
        "published_at",
        "by",
        "note",
        "active",
    }
    assert body["versions"][0]["active"] is True
    assert body["active_version"] == body["versions"][0]["version"]


def test_an_unknown_section_is_a_404_problem(config_as: Caller) -> None:
    client = analyst(config_as)
    for call in (
        client.get("/config/prompts"),
        client.put("/config/prompts/draft", json=DRAFT_BODY),
        client.delete("/config/prompts/draft"),
        client.post("/config/prompts/draft/preview"),
        client.post("/config/prompts/draft/publish", json={"note": NOTE}),
    ):
        assert call.status_code == 404
        assert call.json()["type"].endswith("/not_found")


# ── the draft ───────────────────────────────────────────────────────────────


def test_a_draft_is_saved_and_the_section_comes_back_with_it(
    config_as: Caller,
) -> None:
    client = analyst(config_as)
    etag = client.get("/config/field-sets").json()["draft"]["etag"]
    response = client.put(
        "/config/field-sets/draft", json=DRAFT_BODY, headers={"If-Match": etag}
    )
    assert response.status_code == 200, response.text
    draft = response.json()["draft"]
    assert draft["changes"] == [{"path": "max_required", "before": "18", "after": "12"}]
    assert draft["etag"] != etag
    assert client.get("/config/field-sets").json()["draft"] == draft


def test_a_stale_if_match_is_a_409_and_the_draft_is_untouched(
    config_as: Caller,
) -> None:
    client = analyst(config_as)
    client.put("/config/field-sets/draft", json=DRAFT_BODY)
    response = client.put(
        "/config/field-sets/draft",
        json={"path": "completion_threshold", "after": "0.9"},
        headers={"If-Match": 'W/"field-sets:v6:0"'},
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/conflict")
    assert len(client.get("/config/field-sets").json()["draft"]["changes"]) == 1


def test_a_bad_value_is_a_422_that_names_the_field(config_as: Caller) -> None:
    response = analyst(config_as).put(
        "/config/field-sets/draft",
        json={"path": "completion_threshold", "after": "1.5"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["type"].endswith("/validation_failed")
    assert body["errors"][0]["loc"] == "body.after"
    assert "completion_threshold" in body["errors"][0]["msg"]


def test_the_retention_floor_is_refused_with_its_reason(config_as: Caller) -> None:
    response = analyst(config_as).put(
        "/config/scoring-model/draft", json={"path": "retention_floor", "after": "0.5"}
    )
    assert response.status_code == 422
    assert "Fixed at 40 %" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"after": "12"},
        {"path": "max_required"},
        {"path": "", "after": "12"},
        {"path": "max_required", "after": "12", "surprise": 1},
    ],
)
def test_a_malformed_draft_body_is_a_422(
    config_as: Caller, body: dict[str, Any]
) -> None:
    assert (
        analyst(config_as).put("/config/field-sets/draft", json=body).status_code == 422
    )


@pytest.mark.parametrize("key", ["policy-corpus", "ai-agents"])
def test_a_read_only_section_answers_a_draft_with_409(
    config_as: Caller, key: str
) -> None:
    response = analyst(config_as).put(
        f"/config/{key}/draft", json={"path": "x", "after": "y"}
    )
    assert response.status_code == 409
    assert "read-only here" in response.json()["detail"]


def test_discarding_clears_the_draft(config_as: Caller) -> None:
    client = analyst(config_as)
    client.put("/config/field-sets/draft", json=DRAFT_BODY)
    response = client.delete("/config/field-sets/draft")
    assert response.status_code == 200
    assert response.json()["draft"]["changes"] == []
    assert client.delete("/config/field-sets/draft").status_code == 200


# ── preview ─────────────────────────────────────────────────────────────────


def test_a_preview_has_the_webs_four_keys_and_is_computed(config_as: Caller) -> None:
    client = analyst(config_as)
    client.put("/config/field-sets/draft", json=DRAFT_BODY)
    response = client.post("/config/field-sets/draft/preview", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"section", "open_cases_pinned", "summary", "non_retroactive"}
    assert body["section"] == "field-sets"
    assert body["open_cases_pinned"] == 15
    assert body["non_retroactive"] is True
    assert (
        "24 of 80 seeded cases resolve to more than 12 required fields"
        in body["summary"]
    )
    assert client.post("/config/field-sets/draft/preview").json() == body


def test_previewing_no_draft_is_a_409(config_as: Caller) -> None:
    response = analyst(config_as).post("/config/field-sets/draft/preview", json={})
    assert response.status_code == 409


# ── publish ─────────────────────────────────────────────────────────────────


def test_the_whole_flow_edit_preview_publish(
    config_as: Caller, config_reference: InMemoryReferenceStore
) -> None:
    client = analyst(config_as)
    client.put("/config/field-sets/draft", json=DRAFT_BODY)
    client.put(
        "/config/field-sets/draft",
        json={"path": "completion_threshold", "after": "0.9"},
    )
    etag = client.get("/config/field-sets").json()["draft"]["etag"]
    assert client.post("/config/field-sets/draft/preview", json={}).status_code == 200
    published = client.post(
        "/config/field-sets/draft/publish",
        json={"note": NOTE},
        headers={"If-Match": etag},
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["active_version"] == "v7"
    assert body["draft"]["changes"] == []
    assert body["data"]["completion_threshold"] == 0.9
    assert body["data"]["max_required"] == 12
    assert [(v["version"], v["by"], v["note"]) for v in body["versions"]][0] == (
        "v7",
        "Test fcrm_analyst",
        NOTE,
    )
    assert client.get("/config/field-sets").json() == body


def test_the_reason_is_accepted_as_the_note(config_as: Caller) -> None:
    client = analyst(config_as)
    client.put("/config/field-sets/draft", json=DRAFT_BODY)
    response = client.post("/config/field-sets/draft/publish", json={"reason": NOTE})
    assert response.status_code == 200, response.text
    assert response.json()["versions"][0]["note"] == NOTE


@pytest.mark.parametrize(
    "body", [{}, {"note": "too short"}, {"note": "x" * 19}, {"note": NOTE, "extra": 1}]
)
def test_a_missing_or_short_note_is_a_422_and_nothing_is_published(
    config_as: Caller, body: dict[str, Any]
) -> None:
    client = analyst(config_as)
    client.put("/config/field-sets/draft", json=DRAFT_BODY)
    assert client.post("/config/field-sets/draft/publish", json=body).status_code == 422
    assert client.get("/config/field-sets").json()["active_version"] == "v6"


def test_a_publish_with_no_draft_is_a_409(config_as: Caller) -> None:
    response = analyst(config_as).post(
        "/config/field-sets/draft/publish", json={"note": NOTE}
    )
    assert response.status_code == 409


def test_a_publish_with_a_stale_if_match_is_a_409(config_as: Caller) -> None:
    client = analyst(config_as)
    client.put("/config/field-sets/draft", json=DRAFT_BODY)
    response = client.post(
        "/config/field-sets/draft/publish",
        json={"note": NOTE},
        headers={"If-Match": 'W/"field-sets:v6:0"'},
    )
    assert response.status_code == 409


def test_an_invalid_result_is_a_422_that_names_the_domain(config_as: Caller) -> None:
    client = analyst(config_as)
    client.put(
        "/config/control-inventory/draft",
        json={"path": "CTL-T01.weight", "after": "0.4"},
    )
    response = client.post(
        "/config/control-inventory/draft/publish", json={"note": NOTE}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == "controls.channel"


def test_a_retried_publish_with_its_key_publishes_once(
    config_as: Caller, config_reference: InMemoryReferenceStore
) -> None:
    client = analyst(config_as)
    client.put(
        "/config/jurisdiction-risk/draft",
        json={"path": "fatf_sync", "after": "v2026-10-fixture"},
    )
    headers = {"Idempotency-Key": "publish-0001"}
    first = client.post(
        "/config/jurisdiction-risk/draft/publish", json={"note": NOTE}, headers=headers
    )
    again = client.post(
        "/config/jurisdiction-risk/draft/publish", json={"note": NOTE}, headers=headers
    )
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert len(config_reference.events) == 1
    different = client.post(
        "/config/jurisdiction-risk/draft/publish",
        json={"note": NOTE + " and more"},
        headers=headers,
    )
    assert different.status_code == 409


@pytest.mark.parametrize("key", ["", "has space", "x" * 256, "naïve"])
def test_a_malformed_idempotency_key_is_a_422(config_as: Caller, key: str) -> None:
    client = analyst(config_as)
    client.put("/config/field-sets/draft", json=DRAFT_BODY)
    try:
        response = client.post(
            "/config/field-sets/draft/publish",
            json={"note": NOTE},
            headers={"Idempotency-Key": key},
        )
    except UnicodeEncodeError:
        return  # a non-ASCII header cannot even be sent
    assert response.status_code == 422
    assert client.get("/config/field-sets").json()["active_version"] == "v6"


def test_a_read_only_section_cannot_be_published(config_as: Caller) -> None:
    response = analyst(config_as).post(
        "/config/policy-corpus/draft/publish", json={"note": NOTE}
    )
    assert response.status_code == 409
