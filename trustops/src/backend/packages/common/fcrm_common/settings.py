"""Environment-driven settings shared by every service and worker (design
A §1.4, §11.4).

Each unit subclasses :class:`BaseServiceSettings`, adds its own variables
and, when it needs more than the generic set, extends
``required_settings``. Nothing reads ``os.environ`` at import time: build
the settings in the app factory or lifespan with
``Settings.load()``, which constructs the object *and* calls
:meth:`BaseServiceSettings.fail_fast`, so a container with a missing
secret exits at startup with one message naming every missing variable
instead of failing on its first request (or, worse, falling back to a
hard-coded default, which the cloned AEO identity did).

Field names are the lowercase form of the environment variable names
(``mongodb_uri`` <- ``MONGODB_URI``); matching is case-insensitive.
``env_file`` is ``None`` on purpose: compose injects the variables, and
hooks forbid reading ``.env*`` files.
"""

from __future__ import annotations

from typing import ClassVar, Optional, Self

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH = 32
_LOCAL_ENVS = frozenset({"local", "test", "ci"})


class MissingSettingsError(RuntimeError):
    """One or more required environment variables are missing or invalid.

    Attributes:
        missing: Environment variable names (upper case) that are unset or
            empty.
        invalid: ``"NAME: reason"`` entries for values that are set but
            unusable (for example a too-short secret outside local runs).
    """

    def __init__(self, unit: str, missing: list[str], invalid: list[str]) -> None:
        self.missing = missing
        self.invalid = invalid
        parts: list[str] = []
        if missing:
            parts.append("missing: " + ", ".join(missing))
        if invalid:
            parts.append("invalid: " + "; ".join(invalid))
        super().__init__(
            f"{unit} cannot start, configuration incomplete ({' | '.join(parts)})"
        )


class BaseServiceSettings(BaseSettings):
    """The generic environment contract (design A §11.4, "Shared" and "Per
    service" rows that every unit reads).

    ``required_settings`` lists the variables that must be non-empty;
    ``required_any_of`` lists groups where at least one member must be set
    (identity can be reached through JWKS *or* ``/core/validate``).
    """

    model_config = SettingsConfigDict(
        env_file=None,
        extra="forbid",
        case_sensitive=False,
        validate_default=True,
    )

    required_settings: ClassVar[tuple[str, ...]] = (
        "MONGODB_URI",
        "MONGODB_DB",
        "SERVICE_TOKEN_SECRET",
        "OPA_URL",
    )
    required_any_of: ClassVar[tuple[tuple[str, ...], ...]] = (
        ("IDENTITY_JWKS_URL", "IDENTITY_VALIDATE_URL"),
    )

    app_name: str = "fcrm-service"
    app_root_path: str = ""
    env: str = "local"
    log_level: str = "INFO"
    mongodb_uri: Optional[str] = Field(default=None, repr=False)
    mongodb_db: Optional[str] = None
    rabbitmq_url: Optional[str] = Field(default=None, repr=False)
    redis_url: Optional[str] = Field(default=None, repr=False)
    otel_exporter_otlp_endpoint: str = ""
    identity_jwks_url: Optional[str] = None
    identity_validate_url: Optional[str] = None
    opa_url: Optional[str] = None
    service_token_secret: Optional[SecretStr] = None
    seed_path: Optional[str] = None
    seed_on_empty: bool = False

    @classmethod
    def load(cls) -> Self:
        """Read the environment and :meth:`fail_fast`; the one way a unit
        should build its settings at startup.
        """
        settings = cls()
        settings.fail_fast()
        return settings

    @property
    def is_local(self) -> bool:
        """``ENV`` is a developer or CI environment (``local``, ``test``,
        ``ci``).
        """
        return self.env.lower() in _LOCAL_ENVS

    def _is_set(self, env_name: str) -> bool:
        """Whether the field behind ``env_name`` has a non-empty value."""
        value = getattr(self, env_name.lower(), None)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            return bool(value.strip())
        return value is not None

    def missing_settings(self) -> list[str]:
        """Every required variable (and unmet any-of group) that is unset."""
        missing = [name for name in self.required_settings if not self._is_set(name)]
        for group in self.required_any_of:
            if not any(self._is_set(name) for name in group):
                missing.append(" or ".join(group))
        return missing

    def invalid_settings(self) -> list[str]:
        """Values that are present but unusable. Outside local/CI runs the
        service token secret must be at least 32 characters (HS256 key
        strength).
        """
        problems: list[str] = []
        secret = self.service_token_secret
        if (
            secret is not None
            and not self.is_local
            and len(secret.get_secret_value()) < MIN_SECRET_LENGTH
        ):
            problems.append(
                f"SERVICE_TOKEN_SECRET: shorter than {MIN_SECRET_LENGTH} characters"
            )
        return problems

    def fail_fast(self) -> None:
        """Raise :class:`MissingSettingsError` naming *every* missing or
        invalid variable at once (not pydantic's per-field error, and not
        the first one only).
        """
        missing = self.missing_settings()
        invalid = self.invalid_settings()
        if missing or invalid:
            raise MissingSettingsError(self.app_name, missing, invalid)
