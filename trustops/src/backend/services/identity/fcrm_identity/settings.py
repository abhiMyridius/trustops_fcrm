"""X1's own settings: the shared contract plus the JWT and demo-login
variables (design A §11.4, docker-compose.yaml's ``identity`` service).

Fix carried from the cloned ``evoqidentityservice`` (CLAUDE.md's AEO-reuse
table, bug 3): that clone's ``config.py`` gave ``JWT_SECRET_KEY`` and
``INTERNAL_CLIENT_SECRET`` hard-coded fallback values (a real secret baked
into source, used silently whenever the operator forgot to set the
environment variable). Here ``JWT_KEY_FILE`` and
``DEMO_USERS_PASSWORD_HASH`` have no default at all: :meth:`IdentitySettings.load`
(:meth:`fcrm_common.settings.BaseServiceSettings.fail_fast`) raises and the
process never starts without them, naming exactly what is missing.
"""

from __future__ import annotations

from typing import Any, ClassVar, Optional

from fcrm_common.settings import BaseServiceSettings
from pydantic import SecretStr, field_validator


class IdentitySettings(BaseServiceSettings):
    """X1 identity-service environment contract.

    ``jwt_key_file`` points at the PEM-encoded RSA private key (RS256,
    ``make keys`` writes it; mounted read-only in compose) and
    ``demo_users_password_hash`` is the one bcrypt hash shared by every
    seeded demo user (design A §2.6's "(9) ... one shared demo password
    hash from env"; no plaintext demo password is ever read or stored by
    this service). ``demo_users_enabled`` (``DEMO_USERS_ENABLED``, default
    off) switches on the local demo picker and password-less login, and only
    takes effect when ``ENV`` is local, test or ci (:attr:`demo_login_active`).
    """

    app_name: str = "fcrm-identity"

    required_settings: ClassVar[tuple[str, ...]] = (
        *BaseServiceSettings.required_settings,
        "JWT_KEY_FILE",
        "DEMO_USERS_PASSWORD_HASH",
    )

    jwt_key_file: Optional[str] = None
    jwt_issuer: str = "fcrm-identity"
    jwt_audience: str = "fcrm"
    jwt_access_ttl_s: int = 900
    jwt_refresh_ttl_s: int = 28800
    refresh_cookie_name: str = "fcrm_refresh"
    refresh_cookie_path: str = "/api/fcrm/auth"
    demo_users_password_hash: Optional[SecretStr] = None
    demo_users_enabled: bool = False

    @field_validator("demo_users_enabled", mode="before")
    @classmethod
    def _blank_flag_is_off(cls, value: Any) -> Any:
        """``DEMO_USERS_ENABLED=`` (an empty value, as ``.env.example``
        documents the variables) means off rather than a parse error that
        would stop the api from starting.
        """
        if isinstance(value, str) and not value.strip():
            return False
        return value

    @property
    def demo_login_active(self) -> bool:
        """Whether the demo picker and password-less login are on.

        Both gates must hold: the operator asked for it
        (``DEMO_USERS_ENABLED=true``) *and* ``ENV`` is a developer or CI
        environment (:attr:`is_local`). In any other environment the flag is
        ignored, so a stray ``DEMO_USERS_ENABLED=true`` can never open a
        password-less door outside local use.
        """
        return self.demo_users_enabled and self.is_local
