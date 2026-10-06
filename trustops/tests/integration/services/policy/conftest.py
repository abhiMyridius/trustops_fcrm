"""Fixtures for the S3 configuration tests that need a real MongoDB 8 replica
set: the S3 lifespan on a throw-away database (``live_policy`` in
``tests/conftest.py``) with the calls made as the analyst.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest
from fcrm_common.auth import current_user
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

if TYPE_CHECKING:
    from conftest import LivePolicy


@pytest.fixture
async def live_analyst(live_policy: LivePolicy) -> AsyncIterator[LivePolicy]:
    """The live S3 app answering as an analyst: the configuration routes are
    the analyst's, and the service token ``live_policy.http`` carries is not
    one (it is the service account's, which they refuse).
    """
    principal = Principal(
        sub="u-riyer", roles=[Role.ANALYST], name="Rahul Iyer", typ="access"
    )
    live_policy.app.dependency_overrides[current_user] = lambda: principal
    try:
        yield live_policy
    finally:
        live_policy.app.dependency_overrides.pop(current_user, None)
