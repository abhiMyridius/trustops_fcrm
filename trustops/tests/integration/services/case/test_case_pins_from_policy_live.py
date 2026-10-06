"""S1 pins the versions in force in S3, on a real MongoDB 8 replica set with both
services' real lifespans: S3 publishes a new field-set version through its real
configuration service (the analyst's draft and publish routes), and the next case
the product owner raises over S1's real route pins that version and resolves its
required fields from it, while the case raised before the publish keeps v6.

    docker run -d --name c6e-mongo -p 29117:29117 mongo:8.0 \\
        mongod --replSet rs0 --port 29117 --bind_ip_all
    docker exec c6e-mongo mongosh --quiet --port 29117 --eval \\
        'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:29117"}]}).ok'
    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:29117/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/services/case/test_case_pins_from_policy_live.py

S3 and S1 each get their own database (``tests/integration/services/case/
conftest.py``). Only the message broker and object storage are not real here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.policy_config import PINS_TTL_S
from fcrm_contracts.enums import Role
from fcrm_contracts.versions import PinnedVersions

if TYPE_CHECKING:
    from conftest import LivePo

#: ``LiveS3`` of ``tests/integration/services/case/conftest.py`` (two HTTP clients
#: of S3: ``analyst`` and ``service``, and S3's database ``db``).
LiveS3 = Any

#: Tells ``tests/integration/services/case/conftest.py`` to run S3 beside S1.
ON_POLICY = True

pytestmark = pytest.mark.integration

OWNER = "u-pnair"
NOTE = "Raise the completion threshold to 0.90 for the autumn review cycle"
THRESHOLD = 0.9


async def pins_in_force(s3: LiveS3) -> PinnedVersions:
    """What S3 says a case created now pins (the service account's route)."""
    response = await s3.service.get("/internal/config/pins")
    assert response.status_code == 200, response.text
    return PinnedVersions.model_validate(response.json())


async def publish_field_set(s3: LiveS3) -> None:
    """Publish the next field-set version through S3's configuration routes."""
    draft = await s3.analyst.put(
        "/config/field-sets/draft",
        json={"path": "completion_threshold", "after": str(THRESHOLD)},
    )
    assert draft.status_code == 200, draft.text
    published = await s3.analyst.post(
        "/config/field-sets/draft/publish", json={"note": NOTE}
    )
    assert published.status_code == 200, published.text
    assert published.json()["active_version"] == "v7"


async def test_a_field_set_published_in_s3_reaches_the_next_case_and_not_the_open_one(
    live_po: LivePo, live_s3: LiveS3, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, OWNER)
    client = live_po.client
    store = live_po.case.store

    before = await pins_in_force(live_s3)
    assert before.fieldset_v == 6

    # A case raised before the publish pins what S3 holds in force then.
    first = await client.post("/cases", json={})
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    first_record = await store.get(first_id)
    assert first_record is not None
    assert first_record.pinned_versions == before
    assert first_record.fieldset_v == 6
    required_before = [f.field_id for f in first_record.required_fields]

    # S3 publishes field set v7 (a new threshold) and puts it in force.
    await publish_field_set(live_s3)
    after = await pins_in_force(live_s3)
    assert after == before.model_copy(update={"fieldset_v": 7})

    # S1 reads the versions in force through a cache of a few seconds.
    await asyncio.sleep(PINS_TTL_S + 0.2)

    second = await client.post("/cases", json={})
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    second_record = await store.get(second_id)
    assert second_record is not None
    assert second_record.pinned_versions == after
    assert second_record.fieldset_v == 7
    assert second_record.completion_threshold == THRESHOLD

    # Its required fields are the ones S3 resolves for v7.
    resolved = await live_s3.service.get("/field-sets/v7/resolve")
    assert resolved.status_code == 200, resolved.text
    assert [f.field_id for f in second_record.required_fields] == [
        f["field_id"] for f in resolved.json()["fields"]
    ]
    intake = (await client.get(f"/cases/{second_id}/intake")).json()
    assert intake["field_set_version"] == "v7"

    # The open case keeps its pins and fields for life, and still reads.
    again = await store.get(first_id)
    assert again is not None
    assert again.pinned_versions == before
    assert again.fieldset_v == 6
    assert again.completion_threshold == first_record.completion_threshold
    assert [f.field_id for f in again.required_fields] == required_before
    assert (await client.get(f"/cases/{first_id}/intake")).json()[
        "field_set_version"
    ] == "v6"

    # Both cases announced the pins they were created with.
    created = await live_po.case.db["outbox"].find({"type": "case.created"}).to_list()
    pins_by_case = {
        r["case_id"]: json.loads(r["body"])["payload"]["data"]["pins"] for r in created
    }
    assert pins_by_case[first_id]["fieldset_v"] == 6
    assert pins_by_case[second_id]["fieldset_v"] == 7


async def test_a_case_on_a_published_field_set_reads_after_a_new_s1_process_starts(
    live_po: LivePo, live_s3: LiveS3, act_as: Callable[[Role, str], None]
) -> None:
    from fcrm_case.fieldset_provider import active_field_set, active_pinned_versions
    from fcrm_case.policy_config import PolicyConfig
    from fcrm_common.http import ServiceClient

    act_as(Role.PO, OWNER)
    client = live_po.client
    await publish_field_set(live_s3)
    created = await client.post("/cases", json={})
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]

    # A fresh process holds only the seed's v6; v7 comes from S3 on the first read.
    app = live_po.case.app
    fresh = PolicyConfig(
        seed_field_set=active_field_set(),
        seed_pins=active_pinned_versions(),
        client=ServiceClient(
            "http://policy.test",
            dependency="policy",
            caller="fcrm-case",
            token_secret="x" * 32,
            transport=live_s3.transport,
            retries=0,
        ),
    )
    app.state.policy_config = fresh
    try:
        intake = await client.get(f"/cases/{case_id}/intake")
        assert intake.status_code == 200, intake.text
        assert intake.json()["field_set_version"] == "v7"
        assert fresh.field_set(7).completion_threshold == THRESHOLD
    finally:
        await fresh.aclose()
