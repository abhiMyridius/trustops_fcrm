"""fcrm_common.pagination: opaque cursor round trip."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bson import ObjectId
from fcrm_common.errors import ValidationError
from fcrm_common.pagination import Page, decode_cursor, encode_cursor
from fcrm_contracts.common import Page as ContractPage


@pytest.mark.parametrize(
    "last_id",
    [
        ObjectId("6710f0c2a1b2c3d4e5f60718"),
        "FCRM-2026-0151:49",
        151,
        datetime(2026, 10, 1, 15, 2, 11, 482000, tzinfo=UTC),
    ],
)
def test_cursor_round_trip_preserves_value_and_type(last_id):
    token = encode_cursor(last_id)
    decoded = decode_cursor(token)
    assert decoded == last_id
    assert type(decoded) is type(last_id)


def test_cursor_is_url_safe_without_padding():
    token = encode_cursor("FCRM-2026-0151:49")
    assert "=" not in token
    assert "+" not in token
    assert "/" not in token


@pytest.mark.parametrize("bad", ["", "!!!", "bm90LWpzb24", "eyJ4IjoxfQ", "é"])
def test_decode_cursor_rejects_garbage_as_validation_error(bad):
    with pytest.raises(ValidationError):
        decode_cursor(bad)


def test_page_is_the_contract_model():
    assert Page is ContractPage
    page = Page[str](items=["a"], next_cursor=encode_cursor("a"))
    assert decode_cursor(page.next_cursor or "") == "a"
