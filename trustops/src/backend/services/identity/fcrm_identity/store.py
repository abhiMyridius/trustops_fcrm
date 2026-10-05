"""Mongo access for X1's two collections (design A §2.6).

Same shape as ``fcrm_scoring.store``: a small :class:`Protocol` so
``routes.py`` and its unit tests never need a database — they take
whatever implements :class:`IdentityStore`, the real app wires
:class:`MongoIdentityStore` at startup (``lifespan.py``), and tests wire
:class:`InMemoryIdentityStore`.
"""

from __future__ import annotations

from typing import Optional, Protocol

from fcrm_common.mongo import Repository
from fcrm_contracts.enums import Role

from fcrm_identity.models import RefreshTokenRecord, UserRecord


def login_candidates(login: str) -> list[str]:
    """Seed ids are ``u-riyer``; the web login form's "username" is the
    same id with the ``u-`` prefix stripped (``src/frontend/src/mocks/users.ts``).
    Both spellings, lower-cased, are accepted so the login identifier
    works whichever form a caller sends.
    """
    value = login.strip().lower()
    if not value:
        return []
    if value.startswith("u-"):
        return [value, value[2:]]
    return [value, f"u-{value}"]


class IdentityStore(Protocol):
    """What ``routes.py`` needs from storage."""

    async def find_user_by_login(self, login: str) -> Optional[UserRecord]:
        """The enabled-or-not user matching ``login`` (see
        :func:`login_candidates`), or ``None``.
        """
        ...

    async def user_by_id(self, user_id: str) -> Optional[UserRecord]:
        """The user with this seed id, or ``None``."""
        ...

    async def list_users(self, role: Optional[Role] = None) -> list[UserRecord]:
        """Every *enabled* user in seed (insertion) order; only those holding
        ``role`` when one is given. A disabled account is never listed, so
        it can neither be assigned work nor appear on the demo picker.
        """
        ...

    async def save_refresh(self, record: RefreshTokenRecord) -> None:
        """Persist a freshly minted refresh token."""
        ...

    async def refresh_record(self, jti: str) -> Optional[RefreshTokenRecord]:
        """The stored record for ``jti``, or ``None`` if it was never
        issued or has already expired out of the TTL index.
        """
        ...

    async def revoke_refresh(self, jti: str) -> None:
        """Mark ``jti`` revoked; a no-op if it does not exist."""
        ...


class InMemoryIdentityStore:
    """Used by unit tests: two plain dicts, no Mongo."""

    def __init__(self, users: list[UserRecord]) -> None:
        self._users: dict[str, UserRecord] = {u.id: u for u in users}
        self._refresh: dict[str, RefreshTokenRecord] = {}

    async def find_user_by_login(self, login: str) -> Optional[UserRecord]:
        for candidate in login_candidates(login):
            user = self._users.get(candidate)
            if user is not None:
                return user
        return None

    async def user_by_id(self, user_id: str) -> Optional[UserRecord]:
        return self._users.get(user_id)

    async def list_users(self, role: Optional[Role] = None) -> list[UserRecord]:
        return [
            user
            for user in self._users.values()
            if user.enabled and (role is None or role in user.roles)
        ]

    async def save_refresh(self, record: RefreshTokenRecord) -> None:
        self._refresh[record.jti] = record

    async def refresh_record(self, jti: str) -> Optional[RefreshTokenRecord]:
        return self._refresh.get(jti)

    async def revoke_refresh(self, jti: str) -> None:
        existing = self._refresh.get(jti)
        if existing is not None:
            self._refresh[jti] = existing.model_copy(update={"revoked": True})


class MongoIdentityStore:
    """The real store: ``fcrm_identity.users`` and
    ``fcrm_identity.refresh_tokens``, each keyed by its own id as ``_id``
    (see ``lifespan.py`` for how the repositories are built, indexed and
    seeded).
    """

    def __init__(
        self,
        users: Repository[UserRecord],
        refresh_tokens: Repository[RefreshTokenRecord],
    ) -> None:
        self._users = users
        self._refresh_tokens = refresh_tokens

    async def find_user_by_login(self, login: str) -> Optional[UserRecord]:
        candidates = login_candidates(login)
        if not candidates:
            return None
        return await self._users.find_one({"_id": {"$in": candidates}})

    async def user_by_id(self, user_id: str) -> Optional[UserRecord]:
        return await self._users.find_one({"_id": user_id})

    async def list_users(self, role: Optional[Role] = None) -> list[UserRecord]:
        query: dict[str, object] = {"enabled": True}
        if role is not None:
            query["roles"] = role.value
        # No sort: a freshly seeded collection reads back in seed order, which
        # is the order the demo picker and the roster should show.
        return await self._users.find(query)

    async def save_refresh(self, record: RefreshTokenRecord) -> None:
        await self._refresh_tokens.insert_one(record)

    async def refresh_record(self, jti: str) -> Optional[RefreshTokenRecord]:
        return await self._refresh_tokens.find_one({"_id": jti})

    async def revoke_refresh(self, jti: str) -> None:
        await self._refresh_tokens.update_one({"_id": jti}, {"revoked": True})
