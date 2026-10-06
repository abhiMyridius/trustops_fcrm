"""``fcrm_case.policy_config``: S1 pins the versions S3 holds in force, and
resolves a case against the field-set version it pinned, over a fake S3
(``httpx.MockTransport``).

What is pinned here: the pins are read from S3 with the service token and cached
for a few seconds; S3 slow or failing never fails case creation (the seed's
versions are pinned and a warning names the case and the reason); a published
field set loads once and is kept; a version nobody holds is a 503, never a silent
v6; and a case keeps the pins it was created with whatever is published later.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fcrm_case.fieldset_provider import active_field_set, active_pinned_versions
from fcrm_case.policy_config import (
    PINS_TTL_S,
    build_policy_config,
    field_set_from_policy,
    seed_policy_config,
)
from fcrm_case.settings import CaseSettings
from fcrm_common.errors import UpstreamTimeoutError, UpstreamUnavailableError
from fcrm_contracts.api.policy import FieldDef, FieldSetOut
from fcrm_contracts.enums import Role

if TYPE_CHECKING:
    from conftest import CaseService, FakePolicy

CASE = "FCRM-2026-0200"
OWNER = "u-pnair"
EXTRA = ({"id": "F99", "label": "Sanctions list reviewed"},)


def warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "fcrm_case.policy_config" and r.levelno == logging.WARNING
    ]


# ── the pins in force ───────────────────────────────────────────────────────


class TestPinsInForce:
    async def test_a_new_case_pins_what_s3_holds_in_force(
        self, fake_policy: FakePolicy
    ) -> None:
        fake_policy.publish_field_set(7, completion_threshold=0.9)
        fake_policy.publish_pins(controls_v=10, routing_v=5)
        config = fake_policy.config()
        try:
            pinned = await config.for_new_case(CASE)
        finally:
            await config.aclose()
        assert pinned.pins.controls_v == 10
        assert pinned.pins.routing_v == 5
        assert pinned.pins.fieldset_v == 7
        assert pinned.pins.scoring_v == active_pinned_versions().scoring_v
        assert pinned.field_set.version == 7
        assert pinned.field_set.completion_threshold == 0.9

    async def test_the_call_carries_s1_s_service_token_to_the_internal_route(
        self, fake_policy: FakePolicy
    ) -> None:
        config = fake_policy.config()
        try:
            await config.for_new_case(CASE)
        finally:
            await config.aclose()
        path, authorization = fake_policy.requests[0]
        assert path == "/api/fcrm/policy/internal/config/pins"
        assert authorization is not None
        assert authorization.startswith("Bearer ")

    async def test_the_pins_are_cached_for_a_few_seconds(
        self, fake_policy: FakePolicy
    ) -> None:
        assert 1 <= PINS_TTL_S <= 10
        config = fake_policy.config()
        try:
            first = await config.for_new_case("FCRM-2026-0200")
            fake_policy.publish_pins(controls_v=10)
            second = await config.for_new_case("FCRM-2026-0201")
        finally:
            await config.aclose()
        assert fake_policy.paths().count("/api/fcrm/policy/internal/config/pins") == 1
        assert second.pins == first.pins
        assert second.pins.controls_v == 9

    async def test_an_expired_entry_is_read_again(
        self, fake_policy: FakePolicy
    ) -> None:
        config = fake_policy.config(pins_ttl_s=0)
        try:
            await config.for_new_case("FCRM-2026-0200")
            fake_policy.publish_pins(controls_v=10)
            later = await config.for_new_case("FCRM-2026-0201")
        finally:
            await config.aclose()
        assert fake_policy.paths().count("/api/fcrm/policy/internal/config/pins") == 2
        assert later.pins.controls_v == 10

    async def test_a_field_set_s3_names_is_fetched_once_for_the_process(
        self, fake_policy: FakePolicy
    ) -> None:
        fake_policy.publish_field_set(7)
        config = fake_policy.config(pins_ttl_s=0)
        try:
            await config.for_new_case("FCRM-2026-0200")
            await config.for_new_case("FCRM-2026-0201")
        finally:
            await config.aclose()
        assert fake_policy.paths().count("/api/fcrm/policy/field-sets/v7") == 1


class TestS3FailureNeverFailsCaseCreation:
    @pytest.mark.parametrize(
        ("failure", "reason"),
        [
            (httpx.ReadTimeout("slow"), "UpstreamTimeoutError"),
            (httpx.ConnectError("refused"), "UpstreamUnavailableError"),
            (httpx.Response(503, json={"detail": "down"}), "UpstreamUnavailableError"),
            (httpx.Response(500, json={"detail": "boom"}), "UpstreamUnavailableError"),
            (httpx.Response(200, json={"not": "pins"}), "ValidationError"),
        ],
        ids=["timeout", "refused", "503", "500", "unreadable"],
    )
    async def test_the_case_pins_the_seed_and_the_warning_names_case_and_reason(
        self,
        fake_policy: FakePolicy,
        caplog: pytest.LogCaptureFixture,
        failure: Any,
        reason: str,
    ) -> None:
        fake_policy.publish_field_set(7)
        fake_policy.failure = failure
        config = fake_policy.config()
        try:
            with caplog.at_level(logging.WARNING, logger="fcrm_case.policy_config"):
                pinned = await config.for_new_case(CASE)
        finally:
            await config.aclose()
        assert pinned.pins == active_pinned_versions()
        assert pinned.field_set == active_field_set()
        [warning] = warnings(caplog)
        assert CASE in warning.getMessage()
        assert reason in warning.getMessage()
        assert warning.__dict__["case_id"] == CASE

    async def test_a_field_set_s3_cannot_serve_pins_the_seed_whole(
        self, fake_policy: FakePolicy, caplog: pytest.LogCaptureFixture
    ) -> None:
        # S3 says v7 is in force, then has no v7 to give: mixing v7's pins with
        # the seed's field set would pin a case to a set it was never raised on.
        fake_policy.publish_pins(fieldset_v=7, controls_v=10)
        config = fake_policy.config()
        try:
            with caplog.at_level(logging.WARNING, logger="fcrm_case.policy_config"):
                pinned = await config.for_new_case(CASE)
        finally:
            await config.aclose()
        assert pinned.pins == active_pinned_versions()
        assert pinned.field_set.version == 6
        assert "field set v7" in warnings(caplog)[0].getMessage()

    async def test_with_no_s3_configured_the_seed_is_pinned_without_a_request(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="fcrm_case.policy_config"):
            config = build_policy_config(CaseSettings())
            pinned = await config.for_new_case(CASE)
        assert pinned.pins == active_pinned_versions()
        assert pinned.field_set == active_field_set()
        [warning] = warnings(caplog)
        assert "POLICY_SERVICE_URL" in warning.getMessage()

    async def test_a_url_builds_a_client_to_it(self) -> None:
        config = build_policy_config(
            CaseSettings(
                policy_service_url="http://api:8000/api/fcrm/policy/",
                service_token_secret="x" * 32,  # type: ignore[arg-type]
            )
        )
        try:
            assert config._client is not None
            assert str(config._client._client.base_url).startswith(
                "http://api:8000/api/fcrm/policy"
            )
        finally:
            await config.aclose()


# ── a field set by version ──────────────────────────────────────────────────


class TestFieldSetByVersion:
    async def test_a_version_s3_holds_loads_into_the_domain_model(
        self, fake_policy: FakePolicy
    ) -> None:
        fake_policy.publish_field_set(
            7, extra_always_required=EXTRA, completion_threshold=0.9, max_required=20
        )
        config = fake_policy.config()
        try:
            loaded = await config.load_field_set(7)
            again = await config.load_field_set(7)
        finally:
            await config.aclose()
        seed = active_field_set()
        assert loaded is again
        assert (loaded.version, loaded.max_required) == (7, 20)
        assert loaded.completion_threshold == 0.9
        # The seed's always-required entries may carry a ``note``; S3's contract
        # does not serve it and S1 never reads it, so id and label are compared.
        seeded = [{"id": f["id"], "label": f["label"]} for f in seed.always_required]
        assert loaded.always_required == [*seeded, *EXTRA]
        assert loaded.per_type == seed.per_type
        assert fake_policy.paths() == ["/api/fcrm/policy/field-sets/v7"]
        assert config.field_set(7) is loaded

    async def test_the_seed_s_set_is_local_and_never_asks_s3(
        self, fake_policy: FakePolicy
    ) -> None:
        config = fake_policy.config()
        try:
            assert await config.load_field_set(6) == active_field_set()
        finally:
            await config.aclose()
        assert fake_policy.requests == []

    async def test_an_unknown_version_is_a_503_never_the_seed(
        self, fake_policy: FakePolicy
    ) -> None:
        config = fake_policy.config()
        try:
            with pytest.raises(UpstreamUnavailableError) as raised:
                await config.load_field_set(9)
        finally:
            await config.aclose()
        assert raised.value.status_code == 503
        assert "v9" in str(raised.value)
        assert fake_policy.paths() == ["/api/fcrm/policy/field-sets/v9"]

    async def test_a_version_not_loaded_is_a_503_for_the_synchronous_lookup(
        self, fake_policy: FakePolicy
    ) -> None:
        config = fake_policy.config()
        try:
            with pytest.raises(UpstreamUnavailableError):
                config.field_set(7)
        finally:
            await config.aclose()
        assert fake_policy.requests == []

    def test_with_no_s3_only_the_seed_is_known(self) -> None:
        config = seed_policy_config()
        assert config.field_set(6) == active_field_set()
        with pytest.raises(UpstreamUnavailableError):
            config.field_set(7)

    async def test_with_no_s3_a_load_of_another_version_is_a_503(self) -> None:
        with pytest.raises(UpstreamUnavailableError):
            await seed_policy_config().load_field_set(7)

    async def test_a_timeout_is_a_504_and_loads_nothing(
        self, fake_policy: FakePolicy
    ) -> None:
        fake_policy.publish_field_set(7)
        fake_policy.failure = httpx.ReadTimeout("slow")
        config = fake_policy.config()
        try:
            with pytest.raises(UpstreamTimeoutError):
                await config.load_field_set(7)
            with pytest.raises(UpstreamUnavailableError):
                config.field_set(7)
        finally:
            await config.aclose()

    async def test_s3_answering_another_version_is_not_taken_for_the_one_asked(
        self, fake_policy: FakePolicy
    ) -> None:
        fake_policy.field_sets["v8"] = {**fake_policy.field_sets["v6"], "version": "v7"}
        config = fake_policy.config()
        try:
            with pytest.raises(UpstreamUnavailableError):
                await config.load_field_set(8)
        finally:
            await config.aclose()

    async def test_s3_refusing_the_service_token_is_a_503_not_a_403(
        self, fake_policy: FakePolicy
    ) -> None:
        fake_policy.failure = httpx.Response(403, json={"detail": "no"})
        config = fake_policy.config()
        try:
            with pytest.raises(UpstreamUnavailableError):
                await config.load_field_set(7)
        finally:
            await config.aclose()

    def test_the_mapping_keeps_each_field_s_id_and_label(self) -> None:
        out = FieldSetOut(
            version="v12",
            active=True,
            max_required=3,
            completion_threshold=0.75,
            always_required=[
                FieldDef(field_id="F01", label="One", always_required=True)
            ],
            per_type={
                "new_product": [
                    FieldDef(
                        field_id="F02",
                        label="Two",
                        always_required=False,
                        change_type="new_product",
                    )
                ]
            },
        )
        data = field_set_from_policy(out)
        assert data.version == 12
        assert data.always_required == [{"id": "F01", "label": "One"}]
        assert data.per_type == {"new_product": [{"id": "F02", "label": "Two"}]}

    def test_a_version_that_is_not_v_number_is_refused(self) -> None:
        out = FieldSetOut(
            version="draft",
            active=False,
            max_required=1,
            completion_threshold=0.5,
            always_required=[],
            per_type={},
        )
        with pytest.raises(ValueError, match="invalid literal"):
            field_set_from_policy(out)


# ── through S1's PO routes ──────────────────────────────────────────────────


class TestThroughTheOwnerRoutes:
    def create(self, service: CaseService, act_as: Callable[..., None]) -> str:
        act_as(Role.PO, OWNER)
        response = service.client.post("/cases", json={})
        assert response.status_code == 201, response.text
        case_id: str = response.json()["id"]
        return case_id

    def test_a_case_created_after_a_publish_pins_the_new_version(
        self,
        case_service_on_policy: CaseService,
        fake_policy: FakePolicy,
        act_as: Callable[..., None],
    ) -> None:
        service = case_service_on_policy
        fake_policy.publish_field_set(
            7, extra_always_required=EXTRA, completion_threshold=0.9
        )
        case_id = self.create(service, act_as)
        record = service.store._cases[case_id]
        assert record.pinned_versions.fieldset_v == 7
        assert record.fieldset_v == 7
        assert record.completion_threshold == 0.9
        assert "F99" in [f.field_id for f in record.required_fields]
        created = [e for e in service.store.events if e.type == "case.created"]
        assert created[-1].payload["data"]["pins"]["fieldset_v"] == 7

    def test_an_existing_case_keeps_its_pins_and_fields_after_a_later_publish(
        self,
        case_service_on_policy: CaseService,
        fake_policy: FakePolicy,
        act_as: Callable[..., None],
    ) -> None:
        service = case_service_on_policy
        before = self.create(service, act_as)
        pinned = service.store._cases[before].pinned_versions
        required = [f.field_id for f in service.store._cases[before].required_fields]

        fake_policy.publish_field_set(7, extra_always_required=EXTRA)
        fake_policy.publish_pins(controls_v=10, scoring_v=4)
        after = self.create(service, act_as)

        old = service.store._cases[before]
        assert old.pinned_versions == pinned
        assert old.fieldset_v == 6
        assert [f.field_id for f in old.required_fields] == required
        assert service.client.get(f"/cases/{before}").status_code == 200
        new = service.store._cases[after]
        assert (new.pinned_versions.controls_v, new.pinned_versions.scoring_v) == (
            10,
            4,
        )
        assert new.fieldset_v == 7

    def test_a_case_on_a_published_version_still_opens_after_s1_restarts(
        self,
        case_service_on_policy: CaseService,
        fake_policy: FakePolicy,
        act_as: Callable[..., None],
    ) -> None:
        service = case_service_on_policy
        fake_policy.publish_field_set(7, extra_always_required=EXTRA)
        case_id = self.create(service, act_as)

        # A new process knows only the seed's field set; v7 is fetched from S3 on
        # the first read of the case, and by the dashboard and the list.
        service.client.app.state.policy_config = fake_policy.config()  # type: ignore[attr-defined]
        assert service.client.get(f"/cases/{case_id}").status_code == 200
        service.client.app.state.policy_config = fake_policy.config()  # type: ignore[attr-defined]
        board = service.client.get("/dashboard")
        assert board.status_code == 200
        assert case_id in [row["id"] for row in board.json()["my_cases"]]
        service.client.app.state.policy_config = fake_policy.config()  # type: ignore[attr-defined]
        assert service.client.get("/cases").status_code == 200

    def test_a_case_on_a_version_s3_no_longer_serves_is_a_503_not_the_seed(
        self,
        case_service_on_policy: CaseService,
        fake_policy: FakePolicy,
        act_as: Callable[..., None],
    ) -> None:
        service = case_service_on_policy
        fake_policy.publish_field_set(7)
        case_id = self.create(service, act_as)

        fake_policy.field_sets.pop("v7")
        service.client.app.state.policy_config = fake_policy.config()  # type: ignore[attr-defined]
        response = service.client.get(f"/cases/{case_id}")
        assert response.status_code == 503
        assert response.json()["type"].endswith("/upstream_unavailable")
        assert "v7" in response.json()["detail"]

    def test_s3_down_does_not_fail_raising_a_case(
        self,
        case_service_on_policy: CaseService,
        fake_policy: FakePolicy,
        act_as: Callable[..., None],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        service = case_service_on_policy
        fake_policy.publish_field_set(7)
        fake_policy.failure = httpx.ReadTimeout("slow")
        with caplog.at_level(logging.WARNING, logger="fcrm_case.policy_config"):
            case_id = self.create(service, act_as)
        record = service.store._cases[case_id]
        assert record.pinned_versions == active_pinned_versions()
        assert record.fieldset_v == 6
        assert case_id in warnings(caplog)[0].getMessage()
