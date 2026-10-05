"""fcrm_common.mongo: client options, BSON conversion, model mapping.

Behaviour against a real server (CRUD, pagination, transactions, leases)
is in ``test_common_mongo_live.py``, gated on a replica-set URI.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Optional

import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128
from fcrm_common import mongo
from fcrm_common.mongo import Repository, as_update, from_bson, get_client, to_bson
from fcrm_contracts.enums import Band, ItemStatus
from pydantic import BaseModel, ConfigDict, Field


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    case_id: str
    status: ItemStatus
    rating: Decimal
    due: Optional[date] = None
    at: datetime
    tags: tuple[str, ...] = ()


class Keyed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    band: Band


ITEM = Item(
    item_id="itm_1",
    case_id="FCRM-2026-0151",
    status=ItemStatus.OVERRIDDEN,
    rating=Decimal("4.5"),
    due=date(2026, 10, 9),
    at=datetime(2026, 10, 1, 15, 2, tzinfo=UTC),
    tags=("vendor", "dd"),
)


def test_to_bson_converts_unencodable_types():
    out = to_bson(
        {
            "d": Decimal("7.60"),
            "e": Band.BAND_3,
            "day": date(2026, 10, 9),
            "t": (1, 2),
            "s": {"x"},
            "nested": [{"d": Decimal("1")}],
        }
    )
    assert out["d"] == Decimal128("7.60")
    assert out["e"] == "3"
    assert type(out["e"]) is str
    assert out["day"] == "2026-10-09"
    assert out["t"] == [1, 2]
    assert out["s"] == ["x"]
    assert out["nested"][0]["d"] == Decimal128("1")


def test_from_bson_restores_decimal_and_stringifies_objectid():
    oid = ObjectId()
    out = from_bson({"_id": oid, "r": Decimal128("4.5"), "l": [Decimal128("1.0")]})
    assert out == {"_id": str(oid), "r": Decimal("4.5"), "l": [Decimal("1.0")]}


def test_as_update_wraps_plain_fields_in_set():
    assert as_update({"status": ItemStatus.CONFIRMED}) == {
        "$set": {"status": "confirmed"}
    }


def test_as_update_passes_operator_documents_through():
    update = {"$set": {"a": 1}, "$inc": {"n": 1}}
    assert as_update(update) == update


@pytest.mark.parametrize("bad", [{}, {"$set": {"a": 1}, "b": 2}])
def test_as_update_rejects_empty_or_mixed(bad):
    with pytest.raises(ValueError, match="update"):
        as_update(bad)


def test_repository_document_round_trip_with_computed_id():
    repo = Repository(None, Item, id_of=lambda i: f"{i.case_id}:{i.item_id}")  # type: ignore[arg-type]
    doc = repo.to_document(ITEM)
    assert doc["_id"] == "FCRM-2026-0151:itm_1"
    assert doc["rating"] == Decimal128("4.5")
    assert doc["status"] == "overridden"
    assert repo.from_document(doc) == ITEM  # _id stripped for extra="forbid"


def test_repository_keeps_id_when_model_aliases_it():
    repo = Repository(None, Keyed)  # type: ignore[arg-type]
    doc = repo.to_document(Keyed(id="v3", band=Band.BAND_2))
    assert doc == {"_id": "v3", "band": "2"}
    assert repo.from_document(doc) == Keyed(id="v3", band=Band.BAND_2)


async def test_get_client_is_cached_per_uri_with_design_timeouts(monkeypatch):
    monkeypatch.setattr(mongo, "_REGISTRY", mongo._ClientRegistry())
    uri = "mongodb://127.0.0.1:1/?replicaSet=rs0"
    client = get_client(uri)
    try:
        assert get_client(uri) is client
        options = client.options
        assert options.server_selection_timeout == 5.0
        assert options.pool_options.socket_timeout == 10.0
        assert options.codec_options.tz_aware is True
        assert options.codec_options.uuid_representation == 4  # STANDARD
    finally:
        await mongo.close_clients()
    assert get_client(uri) is not client
    await mongo.close_clients()


async def test_get_client_lets_uri_options_override_defaults(monkeypatch):
    monkeypatch.setattr(mongo, "_REGISTRY", mongo._ClientRegistry())
    client = get_client("mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300")
    try:
        assert client.options.server_selection_timeout == 0.3
        assert client.options.pool_options.socket_timeout == 10.0
    finally:
        await mongo.close_clients()


def test_mongo_lease_rejects_margin_not_below_ttl():
    with pytest.raises(ValueError, match="margin_s"):
        mongo.MongoLease(None, "x", "me", ttl_s=1.0, margin_s=1.0)  # type: ignore[arg-type]


async def test_mongo_client_closes_only_when_the_last_context_exits(monkeypatch):
    monkeypatch.setattr(mongo, "_REGISTRY", mongo._ClientRegistry())
    uri = "mongodb://localhost:1/?replicaSet=rs0"
    async with mongo.mongo_client(uri) as outer:
        async with mongo.mongo_client(uri) as inner:
            assert inner is outer
        # The inner exit must not have closed or dropped the shared client.
        assert mongo._REGISTRY.clients[uri] is outer
        assert mongo._REGISTRY.refs[uri] == 1
    assert uri not in mongo._REGISTRY.clients
    assert uri not in mongo._REGISTRY.refs


async def test_mongo_client_releases_its_reference_when_the_body_raises(monkeypatch):
    monkeypatch.setattr(mongo, "_REGISTRY", mongo._ClientRegistry())
    uri = "mongodb://localhost:1/?replicaSet=rs0"
    with pytest.raises(RuntimeError, match="boom"):
        async with mongo.mongo_client(uri):
            raise RuntimeError("boom")
    assert mongo._REGISTRY.refs == {}
    assert mongo._REGISTRY.clients == {}
