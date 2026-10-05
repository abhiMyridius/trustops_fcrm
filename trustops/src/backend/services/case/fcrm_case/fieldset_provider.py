"""The active field set and pinned-versions snapshot a new case is created
against.

Design A and the blueprint place ``field_set_versions`` in S3 (policy),
and "active version" is likewise something every owning service (S2
scoring, S3 policy/corpus/jurisdictions, the prompts loader) would expose
for H1 to read at run start. None of that cross-service wiring exists yet
in this repo, so this module reads the same seed data directly through
``fcrm_seed`` -- exactly the shortcut ``fcrm_scoring.lifespan`` takes for
its own self-contained config, applied here for values S1 does not
actually own. This is a deliberate, disclosed placeholder: once those
services expose their own "active version" routes, swap these two
functions for ``fcrm_common.http.ServiceClient`` calls and nothing else in
this package changes (``fcrm_case.domain`` only ever sees a
``FieldSetData``/``PinnedVersions``).
"""

from __future__ import annotations

from functools import lru_cache
from types import MappingProxyType

from fcrm_common.errors import UpstreamUnavailableError
from fcrm_contracts.versions import PinnedVersions
from fcrm_seed.loader import load_raw

from fcrm_case.domain.fieldset import FieldSetData
from fcrm_case.seedmodel import field_set_from_seed, pinned_versions_from_seed


@lru_cache(maxsize=1)
def active_field_set() -> FieldSetData:
    """The field-set version pinned by the seed (v6): cached per process,
    matching ``fcrm_seed.loader.load_raw``'s own process-wide cache.
    """
    return field_set_from_seed(load_raw()["field_set"])


def field_set_for(version: int) -> FieldSetData:
    """The field set of ``version``: a case resolves its fields against the
    version it pinned, never whatever is current. Only the seed's set is loaded
    today, so any other version is a deployment fault, not a client error.

    Raises:
        fcrm_common.errors.UpstreamUnavailableError: ``version`` is not the loaded
            field set's.
    """
    current = active_field_set()
    if current.version != version:
        raise UpstreamUnavailableError(
            f"Field set v{version} is not available (v{current.version} is loaded).",
            dependency="field_set",
        )
    return current


@lru_cache(maxsize=1)
def active_pinned_versions() -> PinnedVersions:
    """The versions a newly created case pins (``seed.json["demo"]
    ["versions"]``): scoring v3, corpus v14, controls v9, field set v6,
    jurisdictions 2026-06, routing v4, prompts 2026-09-28 -- the same
    figures every other seeded case and the walkthrough already pin.
    """
    return pinned_versions_from_seed(load_raw()["demo"]["versions"])


@lru_cache(maxsize=1)
def active_type_domains() -> MappingProxyType[str, tuple[str, ...]]:
    """Which risk domains each change type brings into scope
    (``seed.json["type_domains"]``: a vendor onboarding is a channel change, a
    new geography a geography change...), read-only and cached per process.
    The union over a case's confirmed types is its ``domains``.
    """
    raw = load_raw()["type_domains"]
    return MappingProxyType({str(k): tuple(str(d) for d in v) for k, v in raw.items()})
