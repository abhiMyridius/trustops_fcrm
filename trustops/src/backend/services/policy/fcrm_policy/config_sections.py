"""The eight configuration sections as :class:`~fcrm_policy.config.SectionSpec`.

Each section has a canonical *body* (what a snapshot stores) and a *view*
(the ``data`` the web renders, built from the body). Seven sections are built
from the P7 seed for their first snapshot; two of them (the policy corpus and
the AI agents) are not editable here, because their source of truth is not a
value an analyst types: the corpus is ingested by H3/H4 jobs and the agent
tiers are files under ``ai/`` guarded by the eval gate (ADR-04).

What the analyst can edit, by section (``path`` values the draft accepts):

=====================  ======================================================
``field-sets``         ``completion_threshold``, ``max_required``
``categories``         ``fast_lane.volume_under_usd``
``scoring-model``      ``domain_weights.<domain>``
``control-inventory``  ``<control id>.weight``
``routing-rules``      ``recency``, ``nearing``, ``fast_lane_volume``,
                       ``reminder`` (the case SLA is shown, fixed in code)
``jurisdiction-risk``  ``fatf_sync`` (the stored list version to activate)
=====================  ======================================================

The retention floor is fixed at 40 % and the boundary distance belongs to the
scoring model, so a draft that names either is refused with the reason.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, Final, Optional

from fcrm_common.errors import ConflictError, NotFoundError, ValidationError
from fcrm_contracts.api.policy_config import (
    AgentRow,
    AgentsData,
    AlwaysRequiredField,
    AmlCategoryRef,
    CategoriesData,
    CategoryType,
    ConfigBandDef,
    ConfigDraftChange,
    ControlInventoryData,
    ControlRow,
    CorpusSource,
    DomainWeight,
    DomainWeightSum,
    FastLane,
    FieldRef,
    FieldSetData,
    JurisdictionAnchor,
    JurisdictionCurrent,
    JurisdictionData,
    JurisdictionEntry,
    JurisdictionFixture,
    PerTypeFields,
    PolicyCorpusData,
    RoutingParameter,
    RoutingRuleRow,
    RoutingRulesData,
    ScaleAnchor,
    ScoringModelData,
    TypeAmlCategories,
)
from fcrm_contracts.enums import ChangeType, Domain
from fcrm_contracts.sla import CASE_SLA_BUSINESS_DAYS
from fcrm_seed.loader import load_raw

from fcrm_policy.config import (
    Body,
    SectionSpec,
    SeedSnapshot,
    ViewEnv,
    invalid_value,
    next_numeric_version,
    number_text,
    parse_number,
    unknown_path,
)

#: Evidence older than this many months is flagged stale (design A §5.4).
STALE_AFTER_MONTHS: Final = 18
#: A set of weights must sum to 1 within this tolerance (design A §5.2).
WEIGHT_TOLERANCE: Final = 0.001
RETENTION_FLOOR_FIXED: Final = (
    "Fixed at 40 %, on this or any page. Controls mitigate risk and never eliminate it."
)
UNLISTED_DEFAULT: Final = (
    'Unlisted jurisdictions default to "standard" and are shown as such, never '
    "silently."
)

#: Illustrative classification signals (the seed carries no signal table; the
#: page labels them so). Synthetic.
_SIGNALS: Final[dict[str, list[str]]] = {
    "new_product": [
        "new product name or SKU",
        "pricing or limits for a new offering",
        "product sign-off reference",
    ],
    "new_feature": [
        "change to an existing product",
        "feature flag or release note",
        "no new segment or geography",
    ],
    "process_change": [
        "procedure or SOP change",
        "control owner change",
        "automation of a manual step",
    ],
    "vendor_onboarding": [
        "named third party or payout partner",
        "contract or MSA",
        "outsourced service",
    ],
    "new_geography": [
        "new country or corridor",
        "new currency",
        "local licensing",
    ],
    "new_segment": [
        "new customer type",
        "onboarding standard for a new group",
        "segment definition",
    ],
}
_FAST_LANE_CONDITIONS: Final = [
    "Only change type is a vendor renewal, or a feature",
    "No new geography, segment or product",
    "Annual volume under {volume}",
    "Screening agent proposes; analyst confirms (R4)",
]
#: Routing parameters the seed has no table for; the page's defaults.
_ROUTING_PARAMETERS: Final = [
    ("recency", "Evidence recency (stale after)", "18 months"),
    ("nearing", "Nearing-due reminder", "3 days"),
    ("fast_lane_volume", "Fast-lane annual volume", "under $5M"),
    ("reminder", "Condition reminder", "14 days before due"),
]
BOUNDARY_PARAMETER: Final = ("boundary", "Boundary flag distance")
#: The case SLA the page shows in place of the committee quorum (fixed at three
#: by S5's vote rules): S1 computes every due date from it.
SLA_PARAMETER: Final = ("sla", "SLA target for closing a case")
#: A parameter an older stored body still holds that the page no longer shows.
RETIRED_PARAMETERS: Final = frozenset({"quorum"})
_ROUTING_FORMATS: Final[dict[str, tuple[re.Pattern[str], str]]] = {
    "recency": (re.compile(r"^\d{1,3} months?$"), "like '18 months'"),
    "nearing": (re.compile(r"^\d{1,3} days?$"), "like '3 days'"),
    "fast_lane_volume": (
        re.compile(r"^under \$\d+(\.\d+)?[KM]?$"),
        "like 'under $5M'",
    ),
    "reminder": (
        re.compile(r"^\d{1,3} days? before due$"),
        "like '14 days before due'",
    ),
}
_BOUNDARY_REFUSED: Final = (
    "The boundary distance is part of the scoring model; change it in the "
    "Scoring Model section, which publishes it with the band edges."
)


def utc_midnight(day: str) -> datetime:
    """``2026-10-01`` as midnight UTC."""
    return datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC)


def volume_label(usd: float) -> str:
    """``5000000`` as ``$5M``, ``750000`` as ``$750K``, else ``$1,234,567``."""
    if usd >= 1_000_000 and usd % 1_000_000 == 0:
        return f"${int(usd // 1_000_000)}M"
    if usd >= 1_000 and usd % 1_000 == 0:
        return f"${int(usd // 1_000)}K"
    return f"${usd:,.0f}"


def months_between(earlier: date, later: date) -> int:
    """Whole calendar months from ``earlier`` to ``later``."""
    months = (later.year - earlier.year) * 12 + later.month - earlier.month
    return months - 1 if later.day < earlier.day else months


def _change_type_labels() -> Mapping[str, str]:
    labels: Mapping[str, str] = load_raw()["change_types"]
    return labels


def _domain_labels() -> Mapping[str, str]:
    labels: Mapping[str, str] = load_raw()["domains"]
    return labels


class FieldSetsSpec(SectionSpec):
    """Categories & Fields: the field set cases resolve their required fields
    from (design A §2.3). Published as a new ``field_set_versions`` document.
    """

    key = "field-sets"
    title = "Categories & Fields"
    owner = "FCRM Analytics"

    def seed(self, raw: Mapping[str, Any]) -> SeedSnapshot:
        """The seed's field set (v6)."""
        field_set = raw["field_set"]
        body: Body = {
            "max_required": field_set["max_required"],
            "completion_threshold": field_set["completion_threshold"],
            "always_required": [dict(f) for f in field_set["always_required"]],
            "per_type": {
                t: [dict(f) for f in fields]
                for t, fields in field_set["per_type"].items()
            },
        }
        return SeedSnapshot(
            str(field_set["version"]),
            body,
            utc_midnight(raw["meta"]["generated"]),
            self.owner,
        )

    def value_of(self, body: Mapping[str, Any], path: str) -> str:
        """The current threshold or maximum, as text."""
        if path not in ("completion_threshold", "max_required"):
            raise unknown_path(self.key, path)
        return number_text(body[path])

    def normalise(self, body: Mapping[str, Any], path: str, after: str) -> str:
        """A threshold in (0, 1] to two places, or a whole maximum that still
        holds every always-required field.
        """
        if path == "completion_threshold":
            value = parse_number(path, after, low=0, high=1, low_open=True)
            return number_text(round(value, 2))
        if path == "max_required":
            floor = len(body["always_required"])
            value = parse_number(path, after, low=floor, high=100, integer=True)
            return number_text(value)
        raise unknown_path(self.key, path)

    def set_value(self, body: Body, path: str, value: str) -> None:
        """Store the threshold as a float and the maximum as an int."""
        body[path] = int(value) if path == "max_required" else float(value)

    def next_version(self, current: str, body: Mapping[str, Any]) -> str:
        """``v6`` -> ``v7``."""
        return next_numeric_version(current)

    async def view(self, body: Mapping[str, Any], env: ViewEnv) -> FieldSetData:
        """The thresholds, the always-required set, each change type's pack and
        each type's FFIEC AML risk categories."""
        labels = _change_type_labels()
        return FieldSetData(
            max_required=body["max_required"],
            completion_threshold=body["completion_threshold"],
            always_required=[
                AlwaysRequiredField(id=f["id"], label=f["label"], note=f.get("note"))
                for f in body["always_required"]
            ],
            per_type=[
                PerTypeFields(
                    type=ChangeType(t),
                    label=labels.get(t, t),
                    fields=[FieldRef(id=f["id"], label=f["label"]) for f in fields],
                )
                for t, fields in body["per_type"].items()
            ],
            aml_mapping=_aml_mapping(labels),
        )


def _aml_mapping(labels: Mapping[str, str]) -> list[TypeAmlCategories]:
    """Each change type's FFIEC AML risk categories, from the seed's
    ``type_domains``: the same table S1 reads to set a case's domains
    (``fcrm_case.fieldset_provider.active_type_domains``), so the page shows what
    cases are assessed in. Read-only: no draft path edits it.
    """
    raw: Mapping[str, list[str]] = load_raw()["type_domains"]
    domain_labels = _domain_labels()
    return [
        TypeAmlCategories(
            type=ChangeType(t),
            label=label,
            categories=[
                AmlCategoryRef(domain=Domain(d), label=domain_labels.get(d, d))
                for d in raw.get(t, [])
            ],
        )
        for t, label in labels.items()
    ]


class CategoriesSpec(SectionSpec):
    """Classification Rules: which domains each change type brings into scope
    and the screening fast lane (R4).
    """

    key = "categories"
    title = "Classification Rules"
    owner = "FCRM Analytics"
    _PATH = "fast_lane.volume_under_usd"

    def seed(self, raw: Mapping[str, Any]) -> SeedSnapshot:
        """Types and domains from the seed, the illustrative signals, a $5M
        fast lane (``v1``).
        """
        labels = raw["change_types"]
        body: Body = {
            "types": [
                {
                    "type": t,
                    "label": label,
                    "domains": list(raw["type_domains"].get(t, [])),
                    "signals": list(_SIGNALS.get(t, [])),
                }
                for t, label in labels.items()
            ],
            "fast_lane": {
                "volume_under_usd": 5_000_000,
                "conditions": list(_FAST_LANE_CONDITIONS),
            },
        }
        return SeedSnapshot(
            "v1", body, utc_midnight(raw["meta"]["generated"]), self.owner
        )

    def value_of(self, body: Mapping[str, Any], path: str) -> str:
        """The current fast-lane volume, as text."""
        if path != self._PATH:
            raise unknown_path(self.key, path)
        return number_text(body["fast_lane"]["volume_under_usd"])

    def normalise(self, body: Mapping[str, Any], path: str, after: str) -> str:
        """A whole dollar amount above zero."""
        if path != self._PATH:
            raise unknown_path(self.key, path)
        value = parse_number(path, after, low=0, high=1e10, low_open=True, integer=True)
        return number_text(value)

    def set_value(self, body: Body, path: str, value: str) -> None:
        """Store the volume as an int."""
        body["fast_lane"]["volume_under_usd"] = int(value)

    def next_version(self, current: str, body: Mapping[str, Any]) -> str:
        """``v1`` -> ``v2``."""
        return next_numeric_version(current)

    async def view(self, body: Mapping[str, Any], env: ViewEnv) -> CategoriesData:
        """The types and the fast lane with its volume in the third condition."""
        volume = body["fast_lane"]["volume_under_usd"]
        return CategoriesData(
            types=[
                CategoryType(
                    type=ChangeType(t["type"]),
                    label=t["label"],
                    domains=[Domain(d) for d in t["domains"]],
                    signals=list(t["signals"]),
                )
                for t in body["types"]
            ],
            fast_lane=FastLane(
                volume_under_usd=volume,
                conditions=[
                    c.replace("{volume}", volume_label(volume))
                    for c in body["fast_lane"]["conditions"]
                ],
            ),
        )


class ScoringModelSpec(SectionSpec):
    """Scoring Model: domain weights, scales and bands of the deterministic
    engine. Owned by S2 (``scoring_model_versions``), published there through
    its service-only route.
    """

    key = "scoring-model"
    title = "Scoring Model"
    owner = "FCRM Analytics"
    _PREFIX = "domain_weights."

    def seed(self, raw: Mapping[str, Any]) -> SeedSnapshot:
        """The seed's scoring model (v3), approved on its own date."""
        model = {k: copy.deepcopy(v) for k, v in raw["scoring_model"].items()}
        version = str(model.pop("version"))
        return SeedSnapshot(
            version, model, utc_midnight(model["approved"]), model["owner"]
        )

    def _domain(self, body: Mapping[str, Any], path: str) -> str:
        if path == "retention_floor":
            raise ValidationError(
                RETENTION_FLOOR_FIXED,
                errors=[{"loc": "body.path", "msg": RETENTION_FLOOR_FIXED}],
            )
        domain = path.removeprefix(self._PREFIX)
        if not path.startswith(self._PREFIX) or domain not in body["domain_weights"]:
            raise unknown_path(self.key, path)
        return domain

    def value_of(self, body: Mapping[str, Any], path: str) -> str:
        """The current weight of the named domain, as text."""
        return number_text(body["domain_weights"][self._domain(body, path)])

    def normalise(self, body: Mapping[str, Any], path: str, after: str) -> str:
        """A weight above zero and below one, to three places."""
        self._domain(body, path)
        value = parse_number(path, after, low=0, high=1, low_open=True)
        return number_text(round(value, 3))

    def set_value(self, body: Body, path: str, value: str) -> None:
        """Store the weight as a float."""
        body["domain_weights"][self._domain(body, path)] = float(value)

    def validate(self, body: Mapping[str, Any]) -> None:
        """The four weights sum to 1.00 within the tolerance (design A §5.2)."""
        total = sum(body["domain_weights"].values())
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            msg = f"domain weights sum to {total:.3f}, expected 1.000"
            raise ValidationError(
                f"Domain weights must sum to 1.00: they sum to {total:.3f}.",
                errors=[{"loc": "domain_weights", "msg": msg}],
            )

    def next_version(self, current: str, body: Mapping[str, Any]) -> str:
        """``v3`` -> ``v4``."""
        return next_numeric_version(current)

    async def build_body(
        self, body: Mapping[str, Any], changes: list[ConfigDraftChange], env: ViewEnv
    ) -> Body:
        """The changed body, approved today."""
        new_body = await super().build_body(body, changes, env)
        new_body["approved"] = env.today.isoformat()
        return new_body

    async def view(self, body: Mapping[str, Any], env: ViewEnv) -> ScoringModelData:
        """Weights with their labels, the scales, the bands and the retention."""
        labels = _domain_labels()
        return ScoringModelData(
            domain_weights=[
                DomainWeight(domain=Domain(d), label=labels.get(d, d), weight=w)
                for d, w in body["domain_weights"].items()
            ],
            likelihood_scale=_anchors(body["likelihood_scale"]),
            impact_scale=_anchors(body["impact_scale"]),
            bands=[
                ConfigBandDef(
                    band=b["band"],
                    min=b["min"],
                    max=b["max"],
                    route=b["route"],
                    label=b["label"],
                )
                for b in body["bands"]
            ],
            retention_factor=body["retention_factor"],
            retention_floor=body["retention_floor"],
            boundary_flag=body["boundary_flag"],
            rounding=body["rounding"],
            approved=body["approved"],
            owner=body["owner"],
        )


def _anchors(scale: Mapping[str, str]) -> list[ScaleAnchor]:
    return [ScaleAnchor(value=int(v), label=label) for v, label in scale.items()]


class ControlInventorySpec(SectionSpec):
    """Control Inventory: the catalogue weights controls are scored with.
    Published as a new ``control_inventory_versions`` document.
    """

    key = "control-inventory"
    title = "Control Inventory"
    owner = "FCRM Controls"
    _SUFFIX = ".weight"

    def seed(self, raw: Mapping[str, Any]) -> SeedSnapshot:
        """The seed's inventory (v9, 24 controls)."""
        body: Body = {
            "controls": [
                {
                    "control_id": c["id"],
                    "domain": c["domain"],
                    "name": c["name"],
                    "catalogue_weight": c["weight"],
                    "evidence_artifact": c["evidence_artefact"],
                    "last_tested": c["last_tested"],
                }
                for c in raw["controls"]
            ]
        }
        return SeedSnapshot(
            str(raw["meta"]["controls"]),
            body,
            utc_midnight(raw["meta"]["generated"]),
            self.owner,
        )

    def _control(self, body: Mapping[str, Any], path: str) -> dict[str, Any]:
        control_id = path.removesuffix(self._SUFFIX)
        if path.endswith(self._SUFFIX):
            for control in body["controls"]:
                if control["control_id"] == control_id:
                    found: dict[str, Any] = control
                    return found
        raise unknown_path(self.key, path)

    def value_of(self, body: Mapping[str, Any], path: str) -> str:
        """The current catalogue weight of the named control, as text."""
        return number_text(self._control(body, path)["catalogue_weight"])

    def normalise(self, body: Mapping[str, Any], path: str, after: str) -> str:
        """A weight above zero and at most one, to three places."""
        self._control(body, path)
        value = parse_number(path, after, low=0, high=1, low_open=True)
        return number_text(round(value, 3))

    def set_value(self, body: Body, path: str, value: str) -> None:
        """Store the weight as a float."""
        self._control(body, path)["catalogue_weight"] = float(value)

    def validate(self, body: Mapping[str, Any]) -> None:
        """Each domain's weights sum to 1.00 within the tolerance."""
        sums = domain_sums(body["controls"])
        broken = {d: s for d, s in sums.items() if abs(s - 1.0) > WEIGHT_TOLERANCE}
        if broken:
            errors = [
                {
                    "loc": f"controls.{d}",
                    "msg": f"{d} weights sum to {s:.3f}, expected 1.000",
                }
                for d, s in broken.items()
            ]
            names = ", ".join(f"{d} {s:.3f}" for d, s in broken.items())
            raise ValidationError(
                f"Control weights must sum to 1.00 in every domain; off: {names}.",
                errors=errors,
            )

    def next_version(self, current: str, body: Mapping[str, Any]) -> str:
        """``v9`` -> ``v10``."""
        return next_numeric_version(current)

    async def view(self, body: Mapping[str, Any], env: ViewEnv) -> ControlInventoryData:
        """The controls with their stale flag and each domain's weight sum."""
        return ControlInventoryData(
            controls=[
                ControlRow(
                    id=c["control_id"],
                    domain=Domain(c["domain"]),
                    name=c["name"],
                    weight=c["catalogue_weight"],
                    evidence_artefact=c["evidence_artifact"],
                    last_tested=c["last_tested"],
                    stale=_is_stale(c["last_tested"], env.today),
                )
                for c in body["controls"]
            ],
            domain_weight_sums=[
                DomainWeightSum(domain=Domain(d), sum=round(s, 2))
                for d, s in domain_sums(body["controls"]).items()
            ],
        )


def domain_sums(controls: list[dict[str, Any]]) -> dict[str, float]:
    """``{domain: sum of catalogue weights}`` in first-seen order."""
    sums: dict[str, float] = {}
    for control in controls:
        sums[control["domain"]] = (
            sums.get(control["domain"], 0.0) + control["catalogue_weight"]
        )
    return sums


def _is_stale(last_tested: str, today: date) -> bool:
    return months_between(date.fromisoformat(last_tested), today) > STALE_AFTER_MONTHS


class PolicyCorpusSpec(SectionSpec):
    """Policy Corpus: the sources and clause count. Read-only here: sources are
    ingested by H3 and indexed by H4 as jobs, and clause text is never edited.
    """

    key = "policy-corpus"
    title = "Policy Corpus"
    owner = "FCRM Policy"
    editable = False

    def seed(self, raw: Mapping[str, Any]) -> SeedSnapshot:
        """The seed's corpus (v14: nine sources, 197 clauses)."""
        corpus = raw["corpus"]
        body: Body = {
            "clause_count": corpus["clause_count"],
            "ingested": corpus["ingested"],
            "sources": [copy.deepcopy(s) for s in corpus["sources"]],
        }
        return SeedSnapshot(
            str(corpus["version"]), body, utc_midnight(corpus["ingested"]), self.owner
        )

    def value_of(self, body: Mapping[str, Any], path: str) -> str:
        """Nothing here is editable."""
        raise unknown_path(self.key, path)

    def normalise(self, body: Mapping[str, Any], path: str, after: str) -> str:
        """Nothing here is editable."""
        raise unknown_path(self.key, path)

    def set_value(self, body: Body, path: str, value: str) -> None:
        """Nothing here is editable."""
        raise unknown_path(self.key, path)

    def next_version(self, current: str, body: Mapping[str, Any]) -> str:
        """Corpus versions are made by ingestion, not by publishing."""
        raise ConflictError("The corpus version is made by ingestion jobs.")

    async def view(self, body: Mapping[str, Any], env: ViewEnv) -> PolicyCorpusData:
        """The sources with their clause counts."""
        return PolicyCorpusData(
            clause_count=body["clause_count"],
            ingested=body["ingested"],
            sources=[CorpusSource(**s) for s in body["sources"]],
        )


class RoutingRulesSpec(SectionSpec):
    """Rules & Routing: R1-R6 as data and the parameters around them. Owned by
    S2 (``routing_rules_versions``), published there through its service-only
    route. The boundary distance row is the scoring model's, shown here.
    """

    key = "routing-rules"
    title = "Rules & Routing"
    owner = "FCRM Analytics"

    def seed(self, raw: Mapping[str, Any]) -> SeedSnapshot:
        """The seed's routing rules (v4) with the page's default parameters."""
        routing = raw["routing"]
        body: Body = {
            "rules": [{"id": r["id"], "rule": r["rule"]} for r in routing["rules"]],
            "parameters": [
                {"key": k, "label": label, "value": value}
                for k, label, value in _ROUTING_PARAMETERS
            ],
        }
        return SeedSnapshot(
            str(routing["version"]),
            body,
            utc_midnight(raw["meta"]["generated"]),
            self.owner,
        )

    def _parameter(self, body: Mapping[str, Any], path: str) -> dict[str, Any]:
        if path == BOUNDARY_PARAMETER[0]:
            raise ValidationError(
                _BOUNDARY_REFUSED,
                errors=[{"loc": "body.path", "msg": _BOUNDARY_REFUSED}],
            )
        for parameter in body["parameters"]:
            if parameter["key"] == path:
                found: dict[str, Any] = parameter
                return found
        raise unknown_path(self.key, path)

    def value_of(self, body: Mapping[str, Any], path: str) -> str:
        """The current value of the named parameter."""
        return str(self._parameter(body, path)["value"])

    def normalise(self, body: Mapping[str, Any], path: str, after: str) -> str:
        """Each parameter keeps the format the page shows (``18 months``,
        ``under $5M``); a retired one cannot be edited.
        """
        self._parameter(body, path)
        if path in RETIRED_PARAMETERS:
            raise unknown_path(self.key, path)
        text = " ".join(after.split())
        pattern, hint = _ROUTING_FORMATS[path]
        if pattern.match(text) is None:
            raise invalid_value(path, f"{after!r} must look {hint}.")
        return text

    def set_value(self, body: Body, path: str, value: str) -> None:
        """Store the parameter's text."""
        self._parameter(body, path)["value"] = value

    def next_version(self, current: str, body: Mapping[str, Any]) -> str:
        """``v4`` -> ``v5``."""
        return next_numeric_version(current)

    async def view(self, body: Mapping[str, Any], env: ViewEnv) -> RoutingRulesData:
        """The rules, and the parameters led by the scoring model's boundary and
        the case SLA (both shown, neither edited here); a retired parameter an
        older body holds is left out.
        """
        scoring = await env.active_body("scoring-model")
        boundary = RoutingParameter(
            key=BOUNDARY_PARAMETER[0],
            label=BOUNDARY_PARAMETER[1],
            value=number_text(scoring["boundary_flag"]),
            editable=False,
        )
        sla = RoutingParameter(
            key=SLA_PARAMETER[0],
            label=SLA_PARAMETER[1],
            value=f"{CASE_SLA_BUSINESS_DAYS} business days",
            editable=False,
        )
        return RoutingRulesData(
            rules=[RoutingRuleRow(**r) for r in body["rules"]],
            parameters=[
                boundary,
                sla,
                *(
                    RoutingParameter(**p, editable=True)
                    for p in body["parameters"]
                    if p["key"] not in RETIRED_PARAMETERS
                ),
            ],
        )


#: The 1-5 jurisdiction risk scale: each level's label and what it means.
JURISDICTION_ANCHORS: Final = (
    (
        1,
        "Low",
        "Comprehensive AML regime, strong supervisory record, no international "
        "listing.",
    ),
    (2, "Standard", "Established regime with minor identified weaknesses, no listing."),
    (
        3,
        "Elevated",
        "Identified strategic deficiencies or elevated exposure; enhanced monitoring "
        "warranted.",
    ),
    (
        4,
        "High",
        "FATF grey list, or equivalent identified deficiencies under active "
        "remediation.",
    ),
    (
        5,
        "Severe",
        "FATF blacklist, comprehensive sanctions program, or equivalent "
        "prohibition-level exposure.",
    ),
)
_ANCHOR_LABELS: Final = {level: label for level, label, _ in JURISDICTION_ANCHORS}
FATF_PUBLISHER: Final = "Financial Action Task Force"
INTERNAL_SOURCE: Final = "Internal AML jurisdiction methodology"
INTERNAL_PUBLISHER: Final = "FCRM Sanctions"
#: The draft path of an internal rating: ``internal.<id>``.
INTERNAL_PREFIX: Final = "internal."
#: What separates a list version from its internal revision (``v2026-06+i2``).
INTERNAL_REV_MARK: Final = "+i"
#: The bank's own ratings of jurisdictions no FATF list names, spelt as FATF
#: spells them; edited through drafts (``internal.<id>``).
INTERNAL_JURISDICTIONS: Final = (
    ("united-states", "United States", 1),
    ("united-kingdom", "United Kingdom", 1),
    ("canada", "Canada", 1),
    ("germany", "Germany", 1),
    ("france", "France", 1),
    ("japan", "Japan", 1),
    ("australia", "Australia", 1),
    ("switzerland", "Switzerland", 2),
    ("singapore", "Singapore", 2),
    ("hong-kong", "Hong Kong, China", 2),
    ("india", "India", 2),
    ("brazil", "Brazil", 2),
    ("qatar", "Qatar", 2),
    ("south-africa", "South Africa", 3),
    ("saudi-arabia", "Saudi Arabia", 3),
    ("united-arab-emirates", "United Arab Emirates", 3),
    ("indonesia", "Indonesia", 3),
    ("thailand", "Thailand", 3),
    ("mexico", "Mexico", 3),
    ("philippines", "Philippines", 3),
    ("cayman-islands", "Cayman Islands", 3),
    ("colombia", "Colombia", 3),
    ("nigeria", "Nigeria", 3),
    ("panama", "Panama", 3),
)
#: The keys of the FATF list in a body (the rest is the internal ratings).
_FATF_KEYS: Final = (
    "version",
    "published",
    "source",
    "call_for_action",
    "increased_monitoring",
    "added",
    "removed",
)


def _internal_rows(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The body's internal ratings; a body published before they existed reads
    the starting ones."""
    stored = body.get("internal")
    if stored is None:
        return [
            {"id": i, "jurisdiction": name, "rating": rating}
            for i, name, rating in INTERNAL_JURISDICTIONS
        ]
    return [dict(row) for row in stored]


def list_version(version: str) -> str:
    """The FATF list version of a jurisdiction-risk version: ``v2026-06+i2``
    (an internal revision) is list ``v2026-06``."""
    return version.split(INTERNAL_REV_MARK, 1)[0]


def _fatf_listed(body: Mapping[str, Any]) -> set[str]:
    return {*body["call_for_action"], *body["increased_monitoring"]}


class JurisdictionRiskSpec(SectionSpec):
    """Jurisdiction Risk List: the FATF list in force (read-only) and the bank's
    internal ratings of the jurisdictions it does not name, on one 1-5 scale.

    Two edits. ``fatf_sync`` names a stored FATF list version to activate:
    publishing it is the activation (the same transaction and the same
    ``jurisdiction.list_published`` event as S3's activate route), and the
    internal ratings carry over. ``internal.<id>`` changes one internal rating:
    publishing only internal ratings makes an internal revision
    (``<list version>+i<n>``) that activates nothing, so cases keep pinning the
    same FATF list and no watchlist run starts. A jurisdiction an FATF list names
    takes the FATF rating; its internal rating stays, flagged, and cannot be
    edited while it is listed.
    """

    key = "jurisdiction-risk"
    title = "Jurisdiction Risk List"
    owner = "FCRM Sanctions"
    _PATH = "fatf_sync"

    def seed(self, raw: Mapping[str, Any]) -> SeedSnapshot:
        """The seed's current list (``v2026-06``) and the starting internal
        ratings."""
        current = raw["jurisdictions"]["current"]
        body: Body = {
            "version": current["version"],
            "published": current["published"],
            "source": current["source"],
            "call_for_action": list(current["call_for_action"]),
            "increased_monitoring": list(current["increased_monitoring"]),
            "added": list(current["changes"]["added"]),
            "removed": list(current["changes"]["removed"]),
            "internal": _internal_rows({}),
            "internal_rev": 0,
        }
        return SeedSnapshot(
            current["version"], body, utc_midnight(current["published"]), self.owner
        )

    def _internal_row(self, body: Mapping[str, Any], path: str) -> dict[str, Any]:
        """The internal row ``path`` names.

        Raises:
            ValidationError: No such row.
        """
        row_id = path.removeprefix(INTERNAL_PREFIX)
        for row in _internal_rows(body):
            if row["id"] == row_id:
                return row
        raise unknown_path(self.key, path)

    def value_of(self, body: Mapping[str, Any], path: str) -> str:
        """The version of the list in force, or an internal rating."""
        if path == self._PATH:
            return str(body["version"])
        if path.startswith(INTERNAL_PREFIX):
            return number_text(self._internal_row(body, path)["rating"])
        raise unknown_path(self.key, path)

    def normalise(self, body: Mapping[str, Any], path: str, after: str) -> str:
        """The target list version, trimmed (whether it exists is
        :meth:`check_change`'s question), or an internal rating: a whole number
        from 1 to 5 for a jurisdiction no FATF list names.
        """
        if path.startswith(INTERNAL_PREFIX):
            row = self._internal_row(body, path)
            if row["jurisdiction"] in _fatf_listed(body):
                raise invalid_value(
                    path,
                    f"{row['jurisdiction']} is FATF-listed; the FATF rating applies.",
                )
            rating = parse_number(path, after, low=1, high=5, integer=True)
            return number_text(rating)
        if path != self._PATH:
            raise unknown_path(self.key, path)
        target = after.strip()
        if not target or len(target) > 60:
            raise invalid_value(path, "name a stored jurisdiction list version.")
        return target

    def set_value(self, body: Body, path: str, value: str) -> None:
        """Write an internal rating; the target list is built into the whole
        list by :meth:`build_body`."""
        if not path.startswith(INTERNAL_PREFIX):
            raise ConflictError("A jurisdiction publish activates a stored list.")
        rows = _internal_rows(body)
        row_id = path.removeprefix(INTERNAL_PREFIX)
        for row in rows:
            if row["id"] == row_id:
                row["rating"] = int(value)
        body["internal"] = rows

    async def check_change(
        self, body: Mapping[str, Any], path: str, after: str, env: ViewEnv
    ) -> None:
        """A target list must be stored and not the one in force."""
        if path != self._PATH:
            return
        if after == body["version"]:
            raise invalid_value(path, f"{after} is already the list in force.")
        if await env.reference.jurisdiction_list(after) is None:
            raise invalid_value(path, f"no stored jurisdiction list {after!r}.")

    async def build_body(
        self, body: Mapping[str, Any], changes: list[ConfigDraftChange], env: ViewEnv
    ) -> Body:
        """The published body: the internal ratings changed; and, for a
        ``fatf_sync``, the target list's content with what it adds to and
        removes from the list in force (a sync to the list in force keeps it).
        An internal-only change bumps the internal revision.
        """
        new: Body = copy.deepcopy(dict(body))
        new["internal"] = _internal_rows(body)
        new.setdefault("internal_rev", 0)
        internal = [c for c in changes if c.path.startswith(INTERNAL_PREFIX)]
        for change in internal:
            self.set_value(new, change.path, change.after)
        syncs = [c for c in changes if c.path == self._PATH]
        target = syncs[-1].after if syncs else None
        if target is not None and target != body["version"]:
            stored = await env.reference.jurisdiction_list(target)
            if stored is None:
                raise invalid_value(
                    self._PATH, f"no stored jurisdiction list {target!r}."
                )
            now = {*stored.call_for_action, *stored.increased_monitoring}
            before = _fatf_listed(body)
            new.update(
                {
                    "version": stored.version,
                    "published": stored.published,
                    "source": stored.source,
                    "call_for_action": list(stored.call_for_action),
                    "increased_monitoring": list(stored.increased_monitoring),
                    "added": sorted(now - before),
                    "removed": sorted(before - now),
                }
            )
        elif internal:
            new["internal_rev"] = int(new["internal_rev"]) + 1
        return new

    def next_version(self, current: str, body: Mapping[str, Any]) -> str:
        """The list that becomes active; an internal revision of the list in
        force when only internal ratings changed (``v2026-06+i1``)."""
        if str(body["version"]) != list_version(current):
            return str(body["version"])
        return f"{body['version']}{INTERNAL_REV_MARK}{body['internal_rev']}"

    async def view(self, body: Mapping[str, Any], env: ViewEnv) -> JurisdictionData:
        """The list in force, every rated jurisdiction on the 1-5 scale, the
        anchors and the stored fixture list."""
        fixture = await env.reference.jurisdiction_list("fixture")
        current = JurisdictionCurrent(**{k: body[k] for k in _FATF_KEYS})
        if fixture is None:
            note, added = "No fixture list is stored.", []
            fixture_version = ""
        else:
            fixture_version, note = fixture.version, fixture.source
            listed = {*fixture.call_for_action, *fixture.increased_monitoring}
            added = (
                list(body["added"])
                if fixture.version == body["version"]
                else sorted(listed - _fatf_listed(body))
            )
        return JurisdictionData(
            current=current,
            fixture=JurisdictionFixture(
                version=fixture_version, note=note, added=added
            ),
            unlisted_default=UNLISTED_DEFAULT,
            anchors=[
                JurisdictionAnchor(level=level, label=label, definition=definition)
                for level, label, definition in JURISDICTION_ANCHORS
            ],
            entries=_jurisdiction_entries(body),
        )


def _jurisdiction_entries(body: Mapping[str, Any]) -> list[JurisdictionEntry]:
    """One row per jurisdiction, strictest first then by name: the FATF
    blacklist (5) and grey list (4), read-only; the internal ratings of the rest,
    editable. An internal rating of a jurisdiction FATF now lists is kept on its
    row as a note, not shown as a row of its own.
    """
    internal = {row["jurisdiction"]: row for row in _internal_rows(body)}

    def external(name: str, rating: int, source: str) -> JurisdictionEntry:
        held = internal.get(name)
        note = (
            f"Now FATF-listed; the internal rating ({held['rating']} · "
            f"{_ANCHOR_LABELS[int(held['rating'])]}) no longer applies."
            if held is not None
            else None
        )
        return JurisdictionEntry(
            id=f"fatf-{name}",
            jurisdiction=name,
            rating=rating,
            rating_label=_ANCHOR_LABELS[rating],
            source=source,
            publisher=FATF_PUBLISHER,
            source_type="external",
            editable=False,
            path=None,
            note=note,
        )

    blacklist = list(body["call_for_action"])
    greylist = [n for n in body["increased_monitoring"] if n not in blacklist]
    rows = [
        *(external(n, 5, "FATF blacklist (call for action)") for n in blacklist),
        *(external(n, 4, "FATF grey list (increased monitoring)") for n in greylist),
    ]
    listed = {*blacklist, *greylist}
    rows += [
        JurisdictionEntry(
            id=row["id"],
            jurisdiction=row["jurisdiction"],
            rating=int(row["rating"]),
            rating_label=_ANCHOR_LABELS[int(row["rating"])],
            source=INTERNAL_SOURCE,
            publisher=INTERNAL_PUBLISHER,
            source_type="internal",
            editable=True,
            path=f"{INTERNAL_PREFIX}{row['id']}",
            note=None,
        )
        for row in internal.values()
        if row["jurisdiction"] not in listed
    ]
    return sorted(rows, key=lambda r: (-r.rating, r.jurisdiction))


class AiAgentsSpec(SectionSpec):
    """AI Agents: the nine agents, their tiers, tools, gates and budgets.
    Read-only: model ids and tiers live only in ``ai/models/tiers.yaml`` and a
    change goes through the eval gate (ADR-04), not through a form.
    """

    key = "ai-agents"
    title = "AI Agents"
    owner = "FCRM AI governance"
    editable = False

    def seed(self, raw: Mapping[str, Any]) -> SeedSnapshot:
        """The seed's agent table with the budget text and the eval figure of
        each, versioned by the prompts version.
        """
        budgets = raw["budgets"]
        per_agent = raw["evals"]["per_agent"]
        agents = [
            {
                **{k: a[k] for k in ("name", "tier", "model", "fallback")},
                "tools": list(a["tools"]),
                "skills": list(a["skills"]),
                **{k: a[k] for k in ("output", "gate", "eval_set")},
                "budget": _budget_text(budgets.get(a["tier"])),
                "eval_score": per_agent.get(a["name"], {}).get("pct", 0.0),
                "eval_metric": per_agent.get(a["name"], {}).get("metric", ""),
            }
            for a in raw["agents"]
        ]
        per_case = budgets.get("per_case")
        body: Body = {
            "agents": agents,
            "per_case_budget": per_case if isinstance(per_case, int) else 120_000,
        }
        version = str(raw["meta"]["prompts"])
        return SeedSnapshot(version, body, utc_midnight(version), self.owner)

    def value_of(self, body: Mapping[str, Any], path: str) -> str:
        """Nothing here is editable."""
        raise unknown_path(self.key, path)

    def normalise(self, body: Mapping[str, Any], path: str, after: str) -> str:
        """Nothing here is editable."""
        raise unknown_path(self.key, path)

    def set_value(self, body: Body, path: str, value: str) -> None:
        """Nothing here is editable."""
        raise unknown_path(self.key, path)

    def next_version(self, current: str, body: Mapping[str, Any]) -> str:
        """Agent versions are the prompts version, made by the eval gate."""
        raise ConflictError("The agent table changes through the eval gate.")

    async def view(self, body: Mapping[str, Any], env: ViewEnv) -> AgentsData:
        """The agent rows and the per-case token budget."""
        return AgentsData(
            agents=[AgentRow(**a) for a in body["agents"]],
            per_case_budget=body["per_case_budget"],
        )


def _budget_text(budget: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(budget, Mapping):
        return ""
    return (
        f"{budget['tokens_per_run'] // 1000}k tokens · {budget['wall_s']}s · "
        f"{budget['retries']} retry"
    )


SECTIONS: Final[dict[str, SectionSpec]] = {
    spec.key: spec
    for spec in (
        FieldSetsSpec(),
        CategoriesSpec(),
        ScoringModelSpec(),
        ControlInventorySpec(),
        PolicyCorpusSpec(),
        RoutingRulesSpec(),
        JurisdictionRiskSpec(),
        AiAgentsSpec(),
    )
}


def section_spec(key: str) -> SectionSpec:
    """The spec of section ``key``.

    Raises:
        NotFoundError: there is no such section.
    """
    spec = SECTIONS.get(key)
    if spec is None:
        raise NotFoundError(f"No configuration section {key!r}.")
    return spec
