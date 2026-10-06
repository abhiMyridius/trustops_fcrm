"""The versions in force and the field sets, read from S3 (policy).

A configuration publish in S3 (a new field set, control inventory, scoring model,
routing rules or jurisdiction list) must reach the cases created after it and
never the ones already open. S3 serves what is in force at
``GET /internal/config/pins`` (service account only) and every stored field-set
version at ``GET /field-sets/{version}``. :class:`PolicyConfig` reads both through
one bounded :class:`~fcrm_common.http.ServiceClient` (short timeouts, one retry,
S1's service token) and keeps the seed's versions (``fcrm_case.fieldset_provider``)
as the offline fallback.

* **Pins for a new case** (:meth:`PolicyConfig.for_new_case`): cached for
  :data:`PINS_TTL_S` seconds, so a burst of new cases is one S3 call. When S3 is
  unreachable, slow or answers something unreadable, the case pins the seed's
  versions and the fallback is logged as a warning naming the case and the reason:
  raising a case never fails because S3 is slow. A case keeps the pins it was
  created with for life; nothing here is read again for an existing case except
  its field set, by the version it pinned.
* **A field set by version** (:meth:`PolicyConfig.load_field_set`): a published
  version is immutable, so it is fetched once and kept for the life of the
  process. A version neither the seed nor S3 holds is a 503 (a deployment fault),
  never a silent fall back to the seed's v6.

The domain code takes plain callables (``Callable[[int], FieldSetData]``), so the
synchronous :meth:`PolicyConfig.field_set` answers from what is loaded; a service
method awaits :meth:`PolicyConfig.load_field_set` for the version of the case it
holds before it hands that case to a store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Optional

from fcrm_common.cache import InMemoryTTLCache, TTLCache, cache_get, cache_set
from fcrm_common.errors import (
    FcrmError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from fcrm_common.http import ServiceClient, Timeouts
from fcrm_contracts.api.policy import FieldSetOut
from fcrm_contracts.versions import PinnedVersions
from pydantic import SecretStr

from fcrm_case.domain.fieldset import FieldSetData
from fcrm_case.fieldset_provider import active_field_set, active_pinned_versions
from fcrm_case.settings import CaseSettings

logger = logging.getLogger(__name__)

PINS_PATH: Final = "/internal/config/pins"
PINS_CACHE_KEY: Final = "policy:pins"
#: How long the versions in force are reused ("a few seconds").
PINS_TTL_S: Final = 5
#: Raising a case waits at most this long on S3 (per phase) and retries once.
POLICY_TIMEOUTS: Final = Timeouts(connect=1.0, read=3.0, write=3.0, pool=2.0)
POLICY_RETRIES: Final = 1
POLICY_BACKOFF_S: Final = (0.2,)
FIELD_SET_DEPENDENCY: Final = "field_set"


@dataclass(frozen=True)
class NewCaseConfig:
    """What a case being raised pins: the versions in force and the field set
    of the version it pins (always one this process can resolve later).
    """

    pins: PinnedVersions
    field_set: FieldSetData


def field_set_from_policy(out: FieldSetOut) -> FieldSetData:
    """S3's served field set as S1's domain model: ``v7`` becomes ``7`` and each
    field keeps its ``id`` and ``label`` (the per-field flags S3 adds are derived
    from the pack a field sits in, which the domain model already encodes).

    Raises:
        ValueError: ``out.version`` is not ``v<number>``, or the shape does not
            suit :class:`~fcrm_case.domain.fieldset.FieldSetData`.
    """
    return FieldSetData(
        version=int(out.version.lstrip("v")),
        max_required=out.max_required,
        completion_threshold=out.completion_threshold,
        always_required=[
            {"id": f.field_id, "label": f.label} for f in out.always_required
        ],
        per_type={
            change_type: [{"id": f.field_id, "label": f.label} for f in fields]
            for change_type, fields in out.per_type.items()
        },
    )


class PolicyConfig:
    """S1's view of S3's versions in force and field sets.

    Args:
        seed_field_set: The seed's field set (v6): always loaded, and the fallback
            for a case that pins it.
        seed_pins: The seed's versions: what a case pins when S3 cannot say.
        client: A client for S3's base URL (``POLICY_SERVICE_URL``); ``None``
            means S1 knows only the seed.
        cache: Where the versions in force are kept for ``pins_ttl_s``.
        pins_ttl_s: How long the versions in force are reused.
    """

    def __init__(
        self,
        *,
        seed_field_set: FieldSetData,
        seed_pins: PinnedVersions,
        client: Optional[ServiceClient] = None,
        cache: Optional[TTLCache] = None,
        pins_ttl_s: int = PINS_TTL_S,
    ) -> None:
        self._client = client
        self._seed_pins = seed_pins
        self._cache: TTLCache = cache if cache is not None else InMemoryTTLCache(8)
        self._pins_ttl_s = pins_ttl_s
        self._field_sets: dict[int, FieldSetData] = {
            seed_field_set.version: seed_field_set
        }
        self._seed_field_set = seed_field_set

    async def aclose(self) -> None:
        """Close the client's connection pool, if there is one."""
        if self._client is not None:
            await self._client.aclose()

    # -- the versions a new case pins ---------------------------------------------

    async def for_new_case(self, case_id: str) -> NewCaseConfig:
        """The pins in force and the field set of the field-set version they name.

        S3 failing at either step (the pins, or the field set they name) is never
        an error here: the case pins the seed's versions and the warning names
        ``case_id`` and the reason.
        """
        pins = await self._pins_from_policy(case_id)
        if pins is None:
            return self._seed_config()
        try:
            return NewCaseConfig(pins, await self.load_field_set(pins.fieldset_v))
        except FcrmError as exc:
            self._warn_fallback(case_id, f"field set v{pins.fieldset_v}: {exc}")
            return self._seed_config()

    def _seed_config(self) -> NewCaseConfig:
        return NewCaseConfig(self._seed_pins, self._seed_field_set)

    async def _pins_from_policy(self, case_id: str) -> Optional[PinnedVersions]:
        """The versions in force from the cache or S3, or ``None`` (after a
        warning when S3 was asked and failed; silently when there is no S3 to
        ask, which startup already said).
        """
        if self._client is None:
            return None
        cached = await self._cached_pins()
        if cached is not None:
            return cached
        try:
            body = await self._client.request_json(
                "GET", PINS_PATH, timeout=POLICY_TIMEOUTS
            )
            pins = PinnedVersions.model_validate(body)
        except (FcrmError, ValueError) as exc:
            self._warn_fallback(case_id, f"{type(exc).__name__}: {exc}")
            return None
        await cache_set(
            self._cache, PINS_CACHE_KEY, pins.model_dump_json(), self._pins_ttl_s
        )
        return pins

    async def _cached_pins(self) -> Optional[PinnedVersions]:
        raw = await cache_get(self._cache, PINS_CACHE_KEY)
        if raw is None:
            return None
        try:
            return PinnedVersions.model_validate_json(raw)
        except ValueError:
            logger.warning("cached policy pins are unreadable; asking S3 again")
            return None

    def _warn_fallback(self, case_id: str, reason: str) -> None:
        logger.warning(
            "policy pins unavailable for case %s (%s); pinning the seed versions",
            case_id,
            reason,
            extra={"case_id": case_id, "reason": reason, "dependency": "policy"},
        )

    # -- field sets by version ----------------------------------------------------

    def field_set(self, version: int) -> FieldSetData:
        """The field set of ``version`` if it is loaded (the seed's always is).

        Raises:
            fcrm_common.errors.UpstreamUnavailableError: ``version`` was never
                loaded: a deployment fault, not a client error. A caller about
                to use a case's field set awaits :meth:`load_field_set` first.
        """
        loaded = self._field_sets.get(version)
        if loaded is None:
            raise self._unavailable(version)
        return loaded

    async def load_field_set(self, version: int) -> FieldSetData:
        """The field set of ``version``: the seed's, one already loaded, or the
        one S3 holds (fetched once, then kept for the life of the process).

        Raises:
            fcrm_common.errors.UpstreamUnavailableError: Neither the seed nor S3
                holds ``version``, S3 answered something unreadable, or there is
                no S3 to ask.
            fcrm_common.errors.UpstreamTimeoutError: S3 did not answer in time.
        """
        loaded = self._field_sets.get(version)
        if loaded is not None:
            return loaded
        if self._client is None:
            raise self._unavailable(version)
        fetched = await self._fetch_field_set(self._client, version)
        self._field_sets[version] = fetched
        return fetched

    async def load_field_sets(self, versions: set[int]) -> None:
        """:meth:`load_field_set` for each of ``versions`` (a list of cases)."""
        for version in sorted(versions):
            await self.load_field_set(version)

    async def _fetch_field_set(
        self, client: ServiceClient, version: int
    ) -> FieldSetData:
        try:
            body = await client.request_json(
                "GET", f"/field-sets/v{version}", timeout=POLICY_TIMEOUTS
            )
            fetched = field_set_from_policy(FieldSetOut.model_validate(body))
        except (UpstreamUnavailableError, UpstreamTimeoutError):
            raise
        except (FcrmError, ValueError) as exc:
            logger.warning(
                "S3 has no readable field set v%s (%s)",
                version,
                exc,
                extra={"version": version, "dependency": "policy"},
            )
            raise self._unavailable(version) from exc
        if fetched.version != version:
            raise self._unavailable(version)
        return fetched

    def _unavailable(self, version: int) -> UpstreamUnavailableError:
        return UpstreamUnavailableError(
            f"Field set v{version} is not available "
            f"(loaded: {', '.join(f'v{v}' for v in sorted(self._field_sets))}).",
            dependency=FIELD_SET_DEPENDENCY,
        )


def seed_policy_config() -> PolicyConfig:
    """A :class:`PolicyConfig` that knows only the seed: no S3 to ask."""
    return PolicyConfig(
        seed_field_set=active_field_set(), seed_pins=active_pinned_versions()
    )


def build_policy_config(
    settings: CaseSettings, *, client: Optional[ServiceClient] = None
) -> PolicyConfig:
    """The :class:`PolicyConfig` S1 runs with: over ``client`` when given (a test
    wires one over an ASGI transport), else over ``POLICY_SERVICE_URL`` with S1's
    service token, else seed-only with a warning.
    """
    if client is None and settings.policy_service_url:
        secret: Optional[SecretStr] = settings.service_token_secret
        client = ServiceClient(
            settings.policy_service_url.rstrip("/"),
            dependency="policy",
            caller=settings.app_name,
            token_secret=secret.get_secret_value() if secret is not None else None,
            timeouts=POLICY_TIMEOUTS,
            retries=POLICY_RETRIES,
            backoff_s=POLICY_BACKOFF_S,
        )
    if client is None:
        logger.warning(
            "no POLICY_SERVICE_URL configured; new cases pin the seed versions "
            "and only the seed's field set is known",
            extra={"dependency": "policy"},
        )
    return PolicyConfig(
        seed_field_set=active_field_set(),
        seed_pins=active_pinned_versions(),
        client=client,
    )
