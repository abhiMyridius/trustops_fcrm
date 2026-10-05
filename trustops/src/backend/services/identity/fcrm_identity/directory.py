"""Turns stored :class:`~fcrm_identity.models.UserRecord` documents into the
shapes the web app reads (``fcrm_contracts.api.identity``).

Pure functions, no I/O: the routes fetch the records and hand them here, so
the mapping is testable without an app or a database. The demo picker's
labels and order follow ``src/frontend/src/mocks/users.ts`` so the real
picker looks like the one the web was built against.
"""

from __future__ import annotations

from collections.abc import Iterable

from fcrm_contracts.api.identity import (
    DemoUser,
    DemoUserGroup,
    PrincipalOut,
    RosterUser,
)
from fcrm_contracts.enums import Role

from fcrm_identity.models import UserRecord

#: The picker's group order and heading for each role.
GROUP_LABELS: dict[Role, str] = {
    Role.PO: "Product owners",
    Role.ANALYST: "FCRM analysts",
    Role.COMMITTEE: "Risk Committee",
    Role.OBSERVER: "Observer",
}

#: Shown under a name when a stored user has no title of its own (a document
#: written before ``UserRecord.title`` existed).
ROLE_TITLES: dict[Role, str] = {
    Role.PO: "Product Owner",
    Role.ANALYST: "FCRM Analyst",
    Role.COMMITTEE: "Risk Committee",
    Role.OBSERVER: "Observer (harness monitor only)",
    Role.SERVICE: "Service account",
}


def title_for(user: UserRecord) -> str:
    """The user's own title, else the label of their first role."""
    if user.title:
        return user.title
    return ROLE_TITLES.get(user.roles[0], "") if user.roles else ""


def username_of(user_id: str) -> str:
    """The web login form's "username": the seed id without its ``u-``."""
    return user_id.removeprefix("u-")


def principal_out(user: UserRecord) -> PrincipalOut:
    """The login ``user`` and ``GET /me`` body for ``user``."""
    return PrincipalOut(
        id=user.id,
        name=user.name,
        roles=list(user.roles),
        title=title_for(user),
        unit=user.unit,
    )


def roster_user(user: UserRecord) -> RosterUser:
    """One ``GET /users?role=`` entry."""
    return RosterUser(
        id=user.id, name=user.name, roles=list(user.roles), unit=user.unit
    )


def demo_groups(users: Iterable[UserRecord]) -> list[DemoUserGroup]:
    """The demo picker: ``users`` grouped by their first role, in
    :data:`GROUP_LABELS` order; a role with nobody in it is left out.
    """
    by_role: dict[Role, list[DemoUser]] = {role: [] for role in GROUP_LABELS}
    for user in users:
        role = user.roles[0] if user.roles else None
        if role is None or role not in by_role:
            continue
        by_role[role].append(
            DemoUser(
                username=username_of(user.id),
                name=user.name,
                role=role,
                title=title_for(user),
                unit=user.unit,
            )
        )
    return [
        DemoUserGroup(role=role, label=GROUP_LABELS[role], users=members)
        for role, members in by_role.items()
        if members
    ]
