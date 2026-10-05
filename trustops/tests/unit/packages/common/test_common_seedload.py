"""fcrm_common.seedload: SeedReader over fcrm_seed and load-if-empty."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fcrm_common.seedload import SeedReader, load_if_empty


def test_seed_reader_default_reads_packaged_seed():
    seed = SeedReader()
    assert len(seed.cases()) == 80
    assert seed.demo_case()["id"] == "FCRM-2026-0151"
    assert {r["id"] for r in seed.routing_rules()} >= {"R1", "R2"}
    assert seed.controls()


def test_seed_reader_path_override(tmp_path):
    path = tmp_path / "seed.json"
    path.write_text(
        json.dumps({"users": {"people": [{"id": "u-1"}]}}), encoding="utf-8"
    )
    assert SeedReader(str(path)).section("users") == {"people": [{"id": "u-1"}]}


def test_seed_reader_unknown_section_lists_available(tmp_path):
    path = tmp_path / "seed.json"
    path.write_text(json.dumps({"users": {}}), encoding="utf-8")
    with pytest.raises(KeyError, match="users"):
        SeedReader(path).section("nope")


class FakeCollection:
    full_name = "fcrm_identity.users"

    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = list(docs or [])

    async def find_one(self, query, projection=None):
        return self.docs[0] if self.docs else None

    async def insert_many(self, docs, ordered=True):
        self.docs.extend(docs)

        class Result:
            inserted_ids = [d.get("_id") for d in docs]

        return Result()


async def test_load_if_empty_inserts_into_empty_collection():
    coll = FakeCollection()
    count = await load_if_empty(coll, [{"_id": "u-1"}, {"_id": "u-2"}])  # type: ignore[arg-type]
    assert count == 2
    assert [d["_id"] for d in coll.docs] == ["u-1", "u-2"]


async def test_load_if_empty_skips_non_empty_collection():
    coll = FakeCollection([{"_id": "existing"}])
    assert await load_if_empty(coll, [{"_id": "u-1"}]) == 0  # type: ignore[arg-type]
    assert len(coll.docs) == 1


async def test_seed_reader_load_if_empty_delegates():
    coll = FakeCollection()
    assert await SeedReader().load_if_empty(coll, [{"_id": "x"}]) == 1  # type: ignore[arg-type]
