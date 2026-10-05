"""Seed loading at startup (design A §11.3, ADR-16 / D-57).

Every service's lifespan, when ``SEED_ON_EMPTY=true``, loads its slice of
the P7 ``fcrm_seed`` dataset into its own collections *only if they are
empty*, so a restart never duplicates or overwrites data::

    seed = SeedReader(settings.seed_path)
    await load_if_empty(db["users"], seed.section("users")["people"])

:class:`SeedReader` goes through ``fcrm_seed.loader`` (the one place the
seed's shape is parsed) unless ``SEED_PATH`` points at an alternative
``seed.json``, e.g. a fixture in a test.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from fcrm_seed import loader
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import BulkWriteError

from fcrm_common.mongo import Document, Repository, to_bson

logger = logging.getLogger(__name__)

_DUPLICATE_KEY = 11000


async def load_if_empty(
    target: AsyncCollection[Document] | Repository[Any],
    docs: Sequence[Mapping[str, Any]],
) -> int:
    """Insert ``docs`` into an empty collection; returns how many were
    inserted (0 when the collection already had documents).

    Two replicas starting together may both see the collection empty.
    Inserts are unordered and duplicate-key errors are tolerated, so with
    deterministic ``_id`` values in ``docs`` the race is harmless; seed
    documents without an ``_id`` should be given one by the caller for the
    same reason.

    Raises:
        pymongo.errors.BulkWriteError: Any write error other than a
            duplicate key.
    """
    collection = target.collection if isinstance(target, Repository) else target
    if not docs:
        return 0
    if await collection.find_one({}, projection={"_id": 1}) is not None:
        logger.info(
            "seed skipped: collection not empty",
            extra={"collection": collection.full_name},
        )
        return 0
    try:
        result = await collection.insert_many(
            [to_bson(dict(d)) for d in docs], ordered=False
        )
        inserted = len(result.inserted_ids)
    except BulkWriteError as exc:
        errors = exc.details.get("writeErrors", [])
        if any(err.get("code") != _DUPLICATE_KEY for err in errors):
            raise
        inserted = int(exc.details.get("nInserted", 0))
        logger.info(
            "seed raced another replica; duplicates ignored",
            extra={"collection": collection.full_name, "duplicates": len(errors)},
        )
    logger.info(
        "seed loaded",
        extra={"collection": collection.full_name, "inserted": inserted},
    )
    return inserted


class SeedReader:
    """Typed-ish access to the P7 seed for service lifespans.

    Args:
        path: ``SEED_PATH``; ``None`` (the default deployment) reads the
            packaged ``fcrm_seed/seed.json`` through ``fcrm_seed.loader``.
    """

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self._path = Path(path) if path else None
        self._raw: Optional[dict[str, Any]] = None

    @property
    def raw(self) -> dict[str, Any]:
        """The whole seed document (parsed once)."""
        if self._raw is None:
            if self._path is None:
                self._raw = loader.load_raw()
            else:
                self._raw = json.loads(self._path.read_text(encoding="utf-8"))
        return self._raw

    def section(self, name: str) -> Any:
        """A top-level section (``users``, ``controls``, ``routing`` ...).

        Raises:
            KeyError: No such section, with the available names listed.
        """
        try:
            return self.raw[name]
        except KeyError:
            raise KeyError(
                f"seed has no section {name!r}; sections: {sorted(self.raw)}"
            ) from None

    def cases(self) -> list[dict[str, Any]]:
        """All 80 seed cases."""
        cases: list[dict[str, Any]] = self.section("cases")
        return cases

    def demo_case(self) -> dict[str, Any]:
        """The canonical demo case FCRM-2026-0151."""
        demo: dict[str, Any] = self.section("demo")
        return demo

    def scoring_model(self) -> dict[str, Any]:
        """Scoring model v3 as seeded."""
        model: dict[str, Any] = self.section("scoring_model")
        return model

    def routing_rules(self) -> list[dict[str, Any]]:
        """Routing rules v4 (R1-R6)."""
        rules: list[dict[str, Any]] = self.section("routing")["rules"]
        return rules

    def controls(self) -> list[dict[str, Any]]:
        """The control inventory (v9)."""
        controls: list[dict[str, Any]] = self.section("controls")
        return controls

    async def load_if_empty(
        self,
        target: AsyncCollection[Document] | Repository[Any],
        docs: Sequence[Mapping[str, Any]],
    ) -> int:
        """:func:`load_if_empty`, for the ``SeedReader(...).load_if_empty``
        call style in design A §11.3.
        """
        return await load_if_empty(target, docs)
