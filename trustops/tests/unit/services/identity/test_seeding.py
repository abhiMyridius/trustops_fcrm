"""fcrm_identity.seeding: building UserRecords from P7's ``users`` section."""

from __future__ import annotations

from datetime import UTC, datetime

from fcrm_contracts.enums import Role
from fcrm_identity.seeding import seed_user_records
from fcrm_seed.loader import load_raw


def test_seed_user_records_covers_every_seeded_person():
    users_section = load_raw()["users"]
    total = sum(len(users_section[group]) for group in ("po", "analyst", "committee"))
    records = seed_user_records(
        users_section, "demo-hash", created_at=datetime.now(UTC)
    )
    # The seed has no observer; one synthetic observer is added (ADR-19).
    assert len(records) == total + 1


def test_seed_user_records_maps_roles_correctly():
    users_section = {
        "po": [{"id": "u-pnair", "name": "Priya Nair", "unit": "Payments"}],
        "analyst": [{"id": "u-riyer", "name": "Rahul Iyer"}],
        "committee": [{"id": "u-amendes", "name": "Ana Mendes"}],
    }
    records = {
        r.id: r
        for r in seed_user_records(
            users_section, "demo-hash", created_at=datetime.now(UTC)
        )
    }
    assert records["u-pnair"].roles == [Role.PO]
    assert records["u-pnair"].unit == "Payments"
    assert records["u-riyer"].roles == [Role.ANALYST]
    assert records["u-riyer"].unit is None
    assert records["u-amendes"].roles == [Role.COMMITTEE]


def test_seed_user_records_share_the_one_password_hash():
    users_section = {
        "po": [{"id": "u-pnair", "name": "Priya Nair"}],
        "analyst": [{"id": "u-riyer", "name": "Rahul Iyer"}],
        "committee": [],
    }
    records = seed_user_records(
        users_section, "shared-bcrypt-hash", created_at=datetime.now(UTC)
    )
    assert {r.password_hash for r in records} == {"shared-bcrypt-hash"}
    assert all(r.enabled for r in records)


def test_seed_user_records_take_the_title_from_the_seed_role_text():
    users_section = {
        "po": [{"id": "u-pnair", "name": "Priya Nair", "role": "Product Owner"}],
        "analyst": [{"id": "u-riyer", "name": "Rahul Iyer", "role": "FCRM Analyst"}],
        "committee": [
            {"id": "u-amendes", "name": "Ana Mendes", "role": "Risk Committee"}
        ],
    }
    records = {
        r.id: r
        for r in seed_user_records(
            users_section, "demo-hash", created_at=datetime.now(UTC)
        )
    }
    assert records["u-pnair"].title == "Product Owner"
    assert records["u-riyer"].title == "FCRM Analyst"
    assert records["u-amendes"].title == "Risk Committee"


def test_seed_user_records_add_one_synthetic_observer_when_the_seed_has_none():
    records = seed_user_records({}, "demo-hash", created_at=datetime.now(UTC))
    assert [(r.id, r.roles) for r in records] == [("u-observer", [Role.OBSERVER])]
    assert records[0].title == "Observer (harness monitor only)"


def test_seed_user_records_keep_a_seeded_observer_instead_of_adding_one():
    users_section = {
        "observer": [{"id": "u-panel", "name": "Panel", "role": "Observer"}]
    }
    records = seed_user_records(
        users_section, "demo-hash", created_at=datetime.now(UTC)
    )
    assert [r.id for r in records] == ["u-panel"]


def test_seed_user_records_give_the_real_seed_a_title_for_everyone():
    records = seed_user_records(
        load_raw()["users"], "demo-hash", created_at=datetime.now(UTC)
    )
    assert all(r.title for r in records)
