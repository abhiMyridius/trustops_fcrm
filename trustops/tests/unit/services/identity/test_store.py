"""fcrm_identity.store: the login-id normalisation and the in-memory
store used by the route tests (the Mongo-backed store is exercised
through the route tests + a live-Mongo smoke test is out of scope here,
matching ``fcrm_scoring``'s split between ``test_routes.py`` and
``test_mongo_live.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fcrm_contracts.enums import Role
from fcrm_identity.models import RefreshTokenRecord, UserRecord
from fcrm_identity.store import InMemoryIdentityStore, login_candidates


@pytest.mark.parametrize(
    ("login", "expected"),
    [
        ("u-riyer", ["u-riyer", "riyer"]),
        ("riyer", ["riyer", "u-riyer"]),
        ("U-RIYER", ["u-riyer", "riyer"]),
        ("  riyer  ", ["riyer", "u-riyer"]),
        ("", []),
        ("   ", []),
    ],
)
def test_login_candidates(login: str, expected: list[str]):
    assert login_candidates(login) == expected


def _user(user_id: str = "u-riyer") -> UserRecord:
    return UserRecord(
        id=user_id,
        name="Rahul Iyer",
        roles=[Role.ANALYST],
        password_hash="$2b$12$" + "a" * 53,
        enabled=True,
        created_at=datetime.now(UTC),
    )


async def test_find_user_by_login_matches_either_spelling():
    store = InMemoryIdentityStore([_user()])
    assert (await store.find_user_by_login("u-riyer")) is not None
    assert (await store.find_user_by_login("riyer")) is not None
    assert (await store.find_user_by_login("riyer")).id == "u-riyer"


async def test_find_user_by_login_unknown_user_is_none():
    store = InMemoryIdentityStore([_user()])
    assert (await store.find_user_by_login("someone-else")) is None


async def test_user_by_id_round_trips():
    store = InMemoryIdentityStore([_user()])
    found = await store.user_by_id("u-riyer")
    assert found is not None
    assert found.name == "Rahul Iyer"


async def test_refresh_token_lifecycle():
    store = InMemoryIdentityStore([_user()])
    record = RefreshTokenRecord(
        jti="jti-1", sub="u-riyer", exp=datetime.now(UTC) + timedelta(hours=8)
    )
    assert (await store.refresh_record("jti-1")) is None
    await store.save_refresh(record)
    stored = await store.refresh_record("jti-1")
    assert stored is not None
    assert stored.revoked is False
    await store.revoke_refresh("jti-1")
    revoked = await store.refresh_record("jti-1")
    assert revoked is not None
    assert revoked.revoked is True


async def test_revoke_refresh_unknown_jti_is_a_no_op():
    store = InMemoryIdentityStore([_user()])
    await store.revoke_refresh("never-issued")  # must not raise


def _person(user_id: str, role: Role, *, enabled: bool = True) -> UserRecord:
    return _user(user_id).model_copy(update={"roles": [role], "enabled": enabled})


async def test_list_users_returns_enabled_users_in_insertion_order():
    store = InMemoryIdentityStore(
        [
            _person("u-b", Role.ANALYST),
            _person("u-a", Role.PO),
            _person("u-off", Role.PO, enabled=False),
        ]
    )
    assert [u.id for u in await store.list_users()] == ["u-b", "u-a"]


async def test_list_users_filters_by_role():
    store = InMemoryIdentityStore(
        [
            _person("u-b", Role.ANALYST),
            _person("u-a", Role.PO),
            _person("u-c", Role.ANALYST),
        ]
    )
    assert [u.id for u in await store.list_users(Role.ANALYST)] == ["u-b", "u-c"]
    assert await store.list_users(Role.COMMITTEE) == []


async def test_list_users_never_lists_a_disabled_account_of_the_role():
    store = InMemoryIdentityStore([_person("u-off", Role.ANALYST, enabled=False)])
    assert await store.list_users(Role.ANALYST) == []
