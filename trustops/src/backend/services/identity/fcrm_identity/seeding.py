"""Builds the seeded ``UserRecord`` documents from P7's ``users`` section
(design A §2.6: "users seeded from P7 users (11 people) ... with roles
fcrm_po | fcrm_analyst | fcrm_committee and one shared demo password hash
from env").

The seed carries no ``demo_password`` field yet (``fcrm_seed/seed.json``'s
``users`` section is ``{id, name, role, unit?}`` per group), so every
seeded user's ``password_hash`` is the one value from
``DEMO_USERS_PASSWORD_HASH`` — never computed here, never logged, and
required at startup (``settings.py``, ``lifespan.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fcrm_contracts.enums import Role

from fcrm_identity.models import UserRecord

_GROUP_ROLES: dict[str, Role] = {
    "po": Role.PO,
    "analyst": Role.ANALYST,
    "committee": Role.COMMITTEE,
}


def seed_user_records(
    users_section: Mapping[str, list[dict[str, Any]]],
    password_hash: str,
    *,
    created_at: datetime,
) -> list[UserRecord]:
    """One :class:`UserRecord` per seeded person across the ``po``,
    ``analyst`` and ``committee`` groups, every one enabled with
    ``password_hash``.
    """
    records: list[UserRecord] = []
    for group, role in _GROUP_ROLES.items():
        for entry in users_section.get(group, []):
            records.append(
                UserRecord(
                    id=entry["id"],
                    name=entry["name"],
                    roles=[role],
                    unit=entry.get("unit"),
                    password_hash=password_hash,
                    enabled=True,
                    created_at=created_at,
                )
            )
    return records
