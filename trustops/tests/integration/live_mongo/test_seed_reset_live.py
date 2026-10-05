"""The mongosh scripts ``ops/scripts/seed_reset.py`` sends into the mongo
container, run for real (mongosh against a throwaway replica set) over
suffixed databases, so no real data is ever at risk.

Proves what Docker-free unit tests cannot: ``--all`` drops the six app databases
and only empties the audit collections (which must keep existing, with their
indexes), and ``--demo-case`` removes exactly the demo case's documents,
including S5's votes, questions and answers that carry no ``case_id``, while the
other cases stay.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymongo
import pytest

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
MONGOSH = shutil.which("mongosh") or (
    "/opt/homebrew/bin/mongosh" if Path("/opt/homebrew/bin/mongosh").exists() else None
)
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
    pytest.mark.skipif(MONGOSH is None, reason="mongosh not found"),
]

ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "seed_reset", ROOT / "ops" / "scripts" / "seed_reset.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
seed_reset = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed_reset)

SUFFIX = "_resettest"
APP = tuple(f"{name}{SUFFIX}" for name in seed_reset.APP_DATABASES)
AUDIT = f"fcrm_audit{SUFFIX}"
DECISION = f"fcrm_decision{SUFFIX}"
CASE = seed_reset.DEMO_CASE_ID
OTHER = "FCRM-2026-0118"


def _mongosh(script: str) -> str:
    result = subprocess.run(
        [str(MONGOSH), URI, "--quiet", "--eval", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def client() -> Iterator[pymongo.MongoClient[Any]]:
    mongo: pymongo.MongoClient[Any] = pymongo.MongoClient(URI)
    for name in (*APP, AUDIT):
        mongo.drop_database(name)
    yield mongo
    for name in (*APP, AUDIT):
        mongo.drop_database(name)
    mongo.close()


def _populate(mongo: pymongo.MongoClient[Any]) -> None:
    """Two cases' worth of data in every place a case leaves rows."""
    audit = mongo[AUDIT]
    audit["audit_records"].create_index([("case_id", 1), ("seq", 1)], unique=True)
    for case in (CASE, OTHER):
        audit["audit_records"].insert_many(
            [{"case_id": case, "seq": s, "type": "case.created"} for s in (1, 2, 3)]
        )
        audit["proposals"].insert_one({"case_id": case, "agent": "intake"})
    audit["verify_runs"].insert_one({"case_id": CASE, "ok": True})
    audit["idempotency_keys"].insert_one({"_id": "k1"})

    mongo[APP[0]]["documents"].insert_many(
        [{"case_id": CASE, "name": "a"}, {"case_id": CASE, "name": "b"}]
    )
    mongo[APP[0]]["documents"].insert_one({"case_id": OTHER, "name": "c"})
    mongo[APP[2]]["run_events"].insert_one({"case_id": CASE, "kind": "step_done"})
    mongo[APP[2]]["run_events"].insert_one({"case_id": OTHER, "kind": "step_done"})

    decision = mongo[DECISION]
    for case, pack in ((CASE, "pack_demo"), (OTHER, "pack_other")):
        decision["packs"].insert_one({"pack_id": pack, "case_id": case})
        decision["votes"].insert_one({"pack_id": pack, "member_id": "u-amendes"})
        decision["questions"].insert_one(
            {"question_id": f"q_{pack}", "question": {"pack_id": pack}}
        )
        decision["answers"].insert_one(
            {"addendum_id": f"a_{pack}", "answer": {"pack_id": pack}}
        )
        decision["outcomes"].insert_one(
            {"_id": f"{pack}:1", "outcome": {"case_id": case, "pack_id": pack}}
        )


def _count(mongo: pymongo.MongoClient[Any], db: str, name: str, **query: Any) -> int:
    return int(mongo[db][name].count_documents(query))


def test_the_demo_case_script_removes_only_that_cases_rows(
    client: pymongo.MongoClient[Any],
) -> None:
    _populate(client)
    script = seed_reset.mongo_demo_script(
        CASE, databases=(*APP, AUDIT), decision_database=DECISION
    )
    output = _mongosh(script)
    assert f"demo case {CASE}:" in output

    # Gone: everything of the demo case, wherever it sat.
    assert _count(client, AUDIT, "audit_records", case_id=CASE) == 0
    assert _count(client, AUDIT, "proposals", case_id=CASE) == 0
    assert _count(client, AUDIT, "verify_runs", case_id=CASE) == 0
    assert _count(client, APP[0], "documents", case_id=CASE) == 0
    assert _count(client, APP[2], "run_events", case_id=CASE) == 0
    for name in ("packs", "votes", "questions", "answers", "outcomes"):
        assert _count(client, DECISION, name, **_demo_filter(name)) == 0, name

    # Kept: the other case, and anything with no case id at all.
    assert _count(client, AUDIT, "audit_records", case_id=OTHER) == 3
    assert _count(client, AUDIT, "proposals", case_id=OTHER) == 1
    assert _count(client, APP[0], "documents", case_id=OTHER) == 1
    assert _count(client, APP[2], "run_events", case_id=OTHER) == 1
    for name in ("packs", "votes", "questions", "answers", "outcomes"):
        assert _count(client, DECISION, name) == 1, name
    assert _count(client, AUDIT, "idempotency_keys") == 1


def _demo_filter(collection: str) -> dict[str, Any]:
    return {
        "packs": {"case_id": CASE},
        "votes": {"pack_id": "pack_demo"},
        "questions": {"question.pack_id": "pack_demo"},
        "answers": {"answer.pack_id": "pack_demo"},
        "outcomes": {"outcome.case_id": CASE},
    }[collection]


def test_the_demo_case_script_is_a_no_op_the_second_time(
    client: pymongo.MongoClient[Any],
) -> None:
    _populate(client)
    script = seed_reset.mongo_demo_script(
        CASE, databases=(*APP, AUDIT), decision_database=DECISION
    )
    _mongosh(script)
    again = _mongosh(script)
    assert "0 document(s) deleted" in again
    assert _count(client, AUDIT, "audit_records", case_id=OTHER) == 3


def test_the_all_script_drops_the_app_databases_and_only_empties_audit(
    client: pymongo.MongoClient[Any],
) -> None:
    _populate(client)
    script = seed_reset.mongo_all_script(APP, AUDIT)
    output = _mongosh(script)
    assert "dropped" in output

    remaining = set(client.list_database_names())
    assert not remaining & set(APP)

    audit = client[AUDIT]
    names = set(audit.list_collection_names())
    assert {"audit_records", "proposals", "verify_runs", "idempotency_keys"} <= names
    for name in ("audit_records", "proposals", "verify_runs", "idempotency_keys"):
        assert audit[name].count_documents({}) == 0, name
    # The unique {case_id, seq} index survives: the insert-only writer could
    # never recreate it.
    index_keys = [
        tuple(ix["key"].items()) for ix in audit["audit_records"].list_indexes()
    ]
    assert (("case_id", 1), ("seq", 1)) in index_keys


def test_the_all_script_tolerates_audit_collections_that_do_not_exist(
    client: pymongo.MongoClient[Any],
) -> None:
    output = _mongosh(seed_reset.mongo_all_script(APP, AUDIT))
    assert "dropped" in output
    assert AUDIT not in client.list_database_names()
