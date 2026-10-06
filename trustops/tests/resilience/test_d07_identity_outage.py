"""D7: identity (the api process) restarts under an issued token (design A 8.1).

Property: an outage of the token service fails clean and heals without
re-login, and never fails open. While the api is down the gateway answers a
gateway error inside its deadline; when it is back the same token works (the
signing key persists, the verifier fetches the key set again); a tampered token
is still refused.
"""

from __future__ import annotations

import pytest
from resilience import drills
from resilience.stack import Stack

pytestmark = [pytest.mark.resilience, pytest.mark.disruptive, pytest.mark.aws]


@pytest.mark.drill("D7", "api stopped under an issued token; same token works after")
def test_d7_issued_token_fails_clean_then_works_again(stack: Stack) -> None:
    """Injection: ``docker stop`` of the api with an analyst token issued
    earlier; two calls through the gateway with it; ``docker start``.
    Observable: before, ``auth/me`` answers 200; during, both calls answer
    502/503/504 in under 40 s; after, the same token answers 200 for the same
    user and the analyst queue answers 200; a token with an altered signature is
    refused (401 or 403, never 200); the harness is ready again.
    """
    outcome = drills.outage_of_identity(stack)
    assert outcome.before_status == 200
    unclean = [p for p in outcome.during if not drills.is_clean_failure(p)]
    assert unclean == [], unclean
    assert outcome.after_status == 200
    assert outcome.after_user, "auth/me did not name the user"
    assert outcome.queue_status == 200
    assert outcome.forged_status in (401, 403), outcome.forged_status
    assert outcome.harness_ready
