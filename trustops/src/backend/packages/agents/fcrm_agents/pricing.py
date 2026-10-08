"""The USD-per-million-token price rows of ``ai/models/tiers.yaml``
(``prices_usd_per_mtok``), plus a label for every model.

Lived in ``fcrm_evals.usage`` first; moved here (ADR-72) so the agent worker
can price a live job for the ``fcrm_agent_cost_usd_total`` metric without
importing the evals package, which depends on the worker. ``fcrm_evals.usage``
re-exports these names, so its callers are unchanged.

Each row says whether it is ``verified`` against an AWS source (and where, and
when) or ``unverified``; :meth:`PriceTable.note` turns that into the footnote
every cost line carries. Every cost here is an estimate at list prices, never
an invoice figure.

**Model ids never leave this module as text.** Committed files under ``evals/``
may not contain a model id (ADR-04), so every model is reported by the
``label`` that ``tiers.yaml`` gives it (``models.<id>.label``), falling back to
its tier slot (``mid.primary``). The id is only used as a lookup key in memory.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "STATUS_PARTLY_VERIFIED",
    "STATUS_UNKNOWN",
    "STATUS_UNVERIFIED",
    "STATUS_VERIFIED",
    "TOKENS_PER_PRICE_UNIT",
    "UNKNOWN_MODEL_LABEL",
    "ModelPrice",
    "PriceTable",
]

logger = logging.getLogger(__name__)

TOKENS_PER_PRICE_UNIT: Final[float] = 1_000_000.0
"""``tiers.yaml`` prices are USD per this many tokens."""
UNKNOWN_MODEL_LABEL: Final[str] = "unlabelled model"
STATUS_VERIFIED: Final[str] = "verified"
"""A price row read from an official AWS (or provider) source."""
STATUS_UNVERIFIED: Final[str] = "unverified"
"""A price row that keeps an earlier figure and says why (its ``reason``)."""
STATUS_PARTLY_VERIFIED: Final[str] = "partly_verified"
"""The table roll-up when some rows are verified and some are not."""
STATUS_UNKNOWN: Final[str] = "unknown"
"""The roll-up of a table that carries no status at all."""


class ModelPrice(BaseModel):
    """USD per million tokens for one model (``None``: not published), and
    where the figures came from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: float = Field(ge=0.0)
    output: float = Field(default=0.0, ge=0.0)
    cache_read: Optional[float] = Field(default=None, ge=0.0)
    cache_write: Optional[float] = Field(default=None, ge=0.0)
    status: str = STATUS_UNVERIFIED
    source: str = ""
    retrieved_on: str = ""
    reason: str = ""


@dataclass(frozen=True)
class PriceTable:
    """Prices and labels for the models in ``tiers.yaml``.

    ``prices`` and ``labels`` are keyed by model id; ``slots`` maps a model id
    to the tier slots that use it (``mid.primary``) and is the label of last
    resort.
    """

    prices: Mapping[str, ModelPrice]
    labels: Mapping[str, str]
    slots: Mapping[str, str] = field(default_factory=dict)
    region: str = ""

    @classmethod
    def from_tiers(cls, config: Mapping[str, Any]) -> PriceTable:
        """Read ``prices_usd_per_mtok`` (rows and region),
        ``models.<id>.label``, the embeddings label and the tier chains out of a
        parsed ``tiers.yaml``."""
        table: Mapping[str, Any] = config.get("prices_usd_per_mtok", {})
        rows: Mapping[str, Any] = table.get("rows", {})
        prices = {
            model_id: ModelPrice.model_validate(
                {k: v for k, v in row.items() if v is not None}
            )
            for model_id, row in rows.items()
        }
        models: Mapping[str, Any] = config.get("models", {})
        labels = {
            model_id: str(entry["label"])
            for model_id, entry in models.items()
            if isinstance(entry, Mapping) and entry.get("label")
        }
        embeddings = config.get("embeddings")
        if isinstance(embeddings, Mapping) and embeddings.get("label"):
            labels[str(embeddings["model"])] = str(embeddings["label"])
        slots: dict[str, list[str]] = {}
        for tier, entry in config.get("tiers", {}).items():
            chain = [("primary", entry["primary"])] + [
                (f"fallback{i}", model_id)
                for i, model_id in enumerate(entry.get("fallbacks", []), start=1)
            ]
            for slot, model_id in chain:
                slots.setdefault(model_id, []).append(f"{tier}.{slot}")
        return cls(
            prices,
            labels,
            {k: "+".join(v) for k, v in slots.items()},
            region=str(table.get("region", "")),
        )

    @property
    def unverified(self) -> list[str]:
        """Labels (never ids) of the models whose price is not verified."""
        return sorted(
            self.label(model_id)
            for model_id, price in self.prices.items()
            if price.status != STATUS_VERIFIED
        )

    @property
    def status(self) -> str:
        """The roll-up of the rows: ``verified`` when every row is,
        ``unverified`` when none is, ``partly_verified`` otherwise
        (``unknown`` for a table with no rows)."""
        if not self.prices:
            return STATUS_UNKNOWN
        verified = [p.status == STATUS_VERIFIED for p in self.prices.values()]
        if all(verified):
            return STATUS_VERIFIED
        return STATUS_PARTLY_VERIFIED if any(verified) else STATUS_UNVERIFIED

    @property
    def retrieved_on(self) -> str:
        """The date the verified rows were read (the latest, when they differ)."""
        return max(
            (
                p.retrieved_on
                for p in self.prices.values()
                if p.status == STATUS_VERIFIED
            ),
            default="",
        )

    def note(self) -> str:
        """The footnote a cost line carries: what the prices are and how far
        they can be trusted. Names the models that are not verified."""
        where = f", {self.region}" if self.region else ""
        if self.status == STATUS_VERIFIED:
            return f"verified AWS list prices{where}, retrieved {self.retrieved_on}"
        if self.status == STATUS_UNKNOWN:
            return "prices not recorded in tiers.yaml (every cost is 0)"
        listed = ", ".join(self.unverified)
        if self.status == STATUS_UNVERIFIED:
            return f"unverified for: {listed} (every model){where}"
        return (
            f"unverified for: {listed}; the others are verified AWS list prices"
            f"{where}, retrieved {self.retrieved_on}"
        )

    def label(self, model_id: str) -> str:
        """The reportable name of ``model_id`` (never the id itself)."""
        return (
            self.labels.get(model_id)
            or self.slots.get(model_id)
            or (UNKNOWN_MODEL_LABEL)
        )

    def redact(self, text: str) -> str:
        """``text`` with every model id it contains replaced by that model's
        label. An error message from Bedrock can name the model it came from;
        the files a run commits must not (module docstring)."""
        known = {*self.prices, *self.labels, *self.slots}
        for model_id in sorted(known, key=len, reverse=True):
            text = text.replace(model_id, self.label(model_id))
        return text

    def cost(
        self,
        model_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Estimated USD of one call. An unpriced model costs 0 and is
        logged once per call so a missing row is visible, not silent."""
        price = self.prices.get(model_id)
        if price is None:
            logger.warning("no price row for %s; its cost is counted as 0", model_id)
            return 0.0
        uncached = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
        amount = uncached * price.input + output_tokens * price.output
        amount += cache_read_tokens * (
            price.cache_read if price.cache_read is not None else price.input
        )
        amount += cache_write_tokens * (
            price.cache_write if price.cache_write is not None else price.input
        )
        return amount / TOKENS_PER_PRICE_UNIT
