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
    assert len(records) == total


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
