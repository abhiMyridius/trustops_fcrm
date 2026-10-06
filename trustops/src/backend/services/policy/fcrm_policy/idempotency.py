"""``Idempotency-Key`` on S3's publish route (the same rule as S1's internal
writes, ``fcrm_case.idempotency``): a publish that may be retried honours the
header, and the store keeps the key beside its effect in the same transaction
(:mod:`fcrm_policy.config_store`).
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Optional

from fastapi import Header
from fcrm_common.errors import ValidationError

MAX_KEY_LENGTH: Final = 255
#: Printable ASCII with no whitespace: what a UUID or a deterministic id gives.
_KEY_PATTERN: Final = re.compile(r"^[\x21-\x7e]+$")

IdempotencyKeyHeader = Annotated[Optional[str], Header(alias="Idempotency-Key")]


def checked_key(raw: Optional[str]) -> Optional[str]:
    """``raw`` when it is a usable key, ``None`` when the header is absent.

    Raises:
        ValidationError: The header is empty, over :data:`MAX_KEY_LENGTH`, or
            holds whitespace or non-ASCII characters.
    """
    if raw is None:
        return None
    if not raw or len(raw) > MAX_KEY_LENGTH or _KEY_PATTERN.match(raw) is None:
        raise ValidationError(
            "Idempotency-Key must be 1 to 255 printable ASCII characters "
            "with no spaces.",
            errors=[{"loc": "header.Idempotency-Key", "msg": "malformed key"}],
        )
    return raw
