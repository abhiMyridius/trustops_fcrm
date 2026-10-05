"""Cursor encoding for :class:`fcrm_contracts.common.Page` (design A §1.4).

``Page[T]`` itself lives in ``fcrm_contracts.common`` (contracts first);
this module owns only the opaque ``next_cursor`` token: URL-safe base64
(no padding) of the last returned ``_id`` in MongoDB canonical Extended
JSON, so an ``ObjectId``, a string, an int or a datetime ``_id`` decodes
back to exactly the same BSON type and the ``{"_id": {"$gt": ...}}`` seek
in :meth:`fcrm_common.mongo.Repository.find_paginated` stays correct.
"""

from __future__ import annotations

import base64
from datetime import UTC
from typing import Any

from bson import json_util
from bson.errors import BSONError
from bson.json_util import CANONICAL_JSON_OPTIONS
from fcrm_contracts.common import Page

from fcrm_common.errors import ValidationError

__all__ = ["Page", "decode_cursor", "encode_cursor"]

# Datetimes decode tz-aware UTC, like the Mongo client (tz_aware=True).
_JSON_OPTIONS = CANONICAL_JSON_OPTIONS.with_options(tz_aware=True, tzinfo=UTC)


def encode_cursor(last_id: Any) -> str:
    """Opaque cursor token for the page that starts after ``last_id``."""
    text = json_util.dumps({"v": last_id}, json_options=_JSON_OPTIONS)
    return base64.urlsafe_b64encode(text.encode("utf-8")).rstrip(b"=").decode("ascii")


def decode_cursor(token: str) -> Any:
    """The ``_id`` value encoded in ``token``.

    Raises:
        ValidationError: The token was not produced by :func:`encode_cursor`
            (a client-supplied query parameter, so a 422, not a 500).
    """
    try:
        padded = token + "=" * (-len(token) % 4)
        text = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        decoded = json_util.loads(text, json_options=_JSON_OPTIONS)
    # binascii.Error, UnicodeDecodeError and JSONDecodeError are ValueErrors;
    # a bad {"$oid": ...} raises bson's InvalidId (a BSONError).
    except (ValueError, TypeError, BSONError) as exc:
        raise ValidationError(
            "Invalid pagination cursor.",
            errors=[{"loc": "query.cursor", "msg": "malformed cursor"}],
        ) from exc
    if not isinstance(decoded, dict) or "v" not in decoded:
        raise ValidationError(
            "Invalid pagination cursor.",
            errors=[{"loc": "query.cursor", "msg": "malformed cursor"}],
        )
    return decoded["v"]
