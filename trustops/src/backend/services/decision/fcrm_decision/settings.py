"""S5's own settings: the shared contract plus where X1 is.

The committee roster (who a pack is released to, and the names on the votes
screen) comes from X1, and the vote rules (quorum three, simple majority) are
fixed by R2, so there is nothing else to configure.
"""

from __future__ import annotations

from typing import Optional

from fcrm_common.settings import BaseServiceSettings


class DecisionSettings(BaseServiceSettings):
    """Environment settings for the decision-service (S5).

    ``IDENTITY_SERVICE_URL`` is X1's base URL including its prefix (compose:
    ``http://api:8000/api/fcrm/auth``), for the committee roster. When it is not
    set it is derived from ``IDENTITY_JWKS_URL`` or ``IDENTITY_VALIDATE_URL``,
    which already point at X1.
    """

    app_name: str = "fcrm-decision"
    identity_service_url: Optional[str] = None

    @property
    def identity_directory_url(self) -> Optional[str]:
        """Where to ask X1 for the roster, or ``None`` when nothing in the
        environment says where X1 is.
        """
        if self.identity_service_url:
            return self.identity_service_url.rstrip("/")
        derived = (
            (self.identity_jwks_url, "/.well-known/jwks.json"),
            (self.identity_validate_url, "/core/validate"),
        )
        for url, suffix in derived:
            if url and url.rstrip("/").endswith(suffix):
                return url.rstrip("/").removesuffix(suffix)
        return None
