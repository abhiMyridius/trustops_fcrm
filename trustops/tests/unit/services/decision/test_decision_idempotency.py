"""``Idempotency-Key`` handling on its own: key validation, the request
fingerprint, and ``once`` (run the work unless the key already ran this request).
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_common.errors import ValidationError
from fcrm_contracts.api.decision import RevoteRequest, SupersedeRequest
from fcrm_decision.errors import IdempotencyKeyReusedError
from fcrm_decision.idempotency import (
    MAX_KEY_LENGTH,
    checked_key,
    fingerprint,
    once,
)
from fcrm_decision.store_memory import InMemoryDecisionStore


def test_checked_key_absent_header_means_no_key() -> None:
    assert checked_key(None) is None


@pytest.mark.parametrize("raw", ["abc", "step-1", "a" * MAX_KEY_LENGTH, "sha256:ab~_."])
def test_checked_key_accepts_printable_ascii_without_spaces(raw: str) -> None:
    assert checked_key(raw) == raw


@pytest.mark.parametrize(
    "raw", ["", "has space", "tab\there", "naïve", "a" * (MAX_KEY_LENGTH + 1)]
)
def test_checked_key_refuses_a_key_nobody_could_type(raw: str) -> None:
    with pytest.raises(ValidationError):
        checked_key(raw)


def test_fingerprint_binds_kind_target_and_body() -> None:
    body = SupersedeRequest(reason="a reason long enough")
    base = fingerprint("pack.supersede", "pack_1", body)
    assert base == fingerprint("pack.supersede", "pack_1", body)
    assert base != fingerprint("pack.supersede", "pack_2", body)
    assert base != fingerprint("pack.revote", "pack_1", body)
    other = SupersedeRequest(reason="another reason long enough")
    assert base != fingerprint("pack.supersede", "pack_1", other)


def test_fingerprint_is_stable_for_equal_bodies() -> None:
    assert fingerprint("pack.revote", "p", RevoteRequest()) == fingerprint(
        "pack.revote", "p", RevoteRequest(reason="no_majority")
    )


async def test_once_without_a_key_always_produces() -> None:
    store = InMemoryDecisionStore()
    calls: list[int] = []

    async def work(tx: Any) -> int:
        async def produce() -> dict[str, Any]:
            calls.append(1)
            return {"n": len(calls)}

        result, replayed = await once(
            tx, key=None, kind="k", print_="f", produce=produce
        )
        assert replayed is False
        return int(result["n"])

    assert await store.run(work) == 1
    assert await store.run(work) == 2


async def test_once_with_a_key_produces_once_and_replays_the_stored_result() -> None:
    store = InMemoryDecisionStore()
    calls: list[int] = []

    async def work(tx: Any) -> tuple[dict[str, Any], bool]:
        async def produce() -> dict[str, Any]:
            calls.append(1)
            return {"n": len(calls)}

        return await once(tx, key="k1", kind="k", print_="f", produce=produce)

    assert await store.run(work) == ({"n": 1}, False)
    assert await store.run(work) == ({"n": 1}, True)
    assert calls == [1]


@pytest.mark.parametrize(("kind", "print_"), [("other", "f"), ("k", "other")])
async def test_once_refuses_a_key_reused_for_a_different_request(
    kind: str, print_: str
) -> None:
    store = InMemoryDecisionStore()

    async def first(tx: Any) -> None:
        async def produce() -> dict[str, Any]:
            return {}

        await once(tx, key="k1", kind="k", print_="f", produce=produce)

    async def second(tx: Any) -> None:
        async def produce() -> dict[str, Any]:
            return {}

        await once(tx, key="k1", kind=kind, print_=print_, produce=produce)

    await store.run(first)
    with pytest.raises(IdempotencyKeyReusedError):
        await store.run(second)


async def test_once_a_failed_request_leaves_no_key_behind() -> None:
    store = InMemoryDecisionStore()

    async def failing(tx: Any) -> None:
        async def produce() -> dict[str, Any]:
            raise RuntimeError("the work failed")

        await once(tx, key="k1", kind="k", print_="f", produce=produce)

    with pytest.raises(RuntimeError, match="the work failed"):
        await store.run(failing)

    async def retry(tx: Any) -> bool:
        async def produce() -> dict[str, Any]:
            return {"ok": True}

        _, replayed = await once(tx, key="k1", kind="k", print_="f", produce=produce)
        return replayed

    assert await store.run(retry) is False  # the key was never stored
