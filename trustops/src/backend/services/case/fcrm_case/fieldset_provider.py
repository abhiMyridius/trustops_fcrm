"""The seed's field set and pinned-versions snapshot: S1's offline fallback.

The versions in force and every published field set live in S3 (policy);
:mod:`fcrm_case.policy_config` reads them through a bounded service client
(``GET /internal/config/pins``, ``GET /field-sets/{version}``) and uses what is
here when S3 cannot answer, or when no ``POLICY_SERVICE_URL`` is configured. Here
the same seed data is read directly through ``fcrm_seed``, as ``fcrm_scoring``
does for its own self-contained configuration. ``fcrm_case.domain`` only ever sees
a ``FieldSetData``/``PinnedVersions``.
"""

from __future__ import annotations

from functools import lru_cache
from types import MappingProxyType

from fcrm_contracts.versions import PinnedVersions
from fcrm_seed.loader import load_raw

from fcrm_case.domain.fieldset import FieldSetData
from fcrm_case.seedmodel import field_set_from_seed, pinned_versions_from_seed


@lru_cache(maxsize=1)
def active_field_set() -> FieldSetData:
    """The field-set version pinned by the seed (v6): cached per process,
    matching ``fcrm_seed.loader.load_raw``'s own process-wide cache. The fallback
    :class:`~fcrm_case.policy_config.PolicyConfig` always holds.
    """
    return field_set_from_seed(load_raw()["field_set"])


@lru_cache(maxsize=1)
def active_pinned_versions() -> PinnedVersions:
    """The versions the seeded cases pin (``seed.json["demo"]["versions"]``):
    scoring v3, corpus v14, controls v9, field set v6, jurisdictions 2026-06,
    routing v4, prompts 2026-09-28 -- the same figures the walkthrough shows. A new
    case pins them only when S3 cannot say what is in force.
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
