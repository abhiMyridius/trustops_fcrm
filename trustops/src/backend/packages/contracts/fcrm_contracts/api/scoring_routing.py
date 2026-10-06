"""S2's routing-rules read and its service-only publish routes.

``GET scoring/routing-rules/{version}`` is the web's route (ADR-30): the rules
as ``{id, rule}`` rows next to the version they belong to. The two
``/internal`` routes are how S3's configuration screens put a published
section into force in S2, which owns the scoring models and routing rules a
case pins: S3 holds the draft and the history, S2 holds the version a case
scores against.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from fcrm_contracts.api.policy_config import RoutingRuleRow
from fcrm_contracts.api.scoring import RoutingRulesVersion, ScoringModelVersion


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutingRulesOut(_Strict):
    """A routing rules version: ``version`` is the label cases pin (``v4``)."""

    version: str
    rules: list[RoutingRuleRow]
    parameters: dict[str, str] = Field(default_factory=dict)
    committee_quorum: int
    active: bool


class ScoringModelPublishIn(_Strict):
    """``POST /internal/models``: the model that becomes the active one.
    ``base_version`` is the version the caller believes is active; S2 refuses
    the call when it is not (a concurrent publish moved it).
    """

    base_version: int
    published_by: str
    model: ScoringModelVersion


class RoutingRulesPublishIn(_Strict):
    """``POST /internal/routing-rules``: the rules version that becomes the
    active one, under the same ``base_version`` rule as the model.
    """

    base_version: int
    published_by: str
    routing: RoutingRulesVersion


class PublishedVersionOut(_Strict):
    """What a publish answers: the version now active and whether this call
    wrote it (``False`` for a repeat of the same publish).
    """

    version: int
    created: bool
