"""S3's configuration domain: sections, snapshots, drafts and their rules.

The analyst owns configuration (D-03). Each of the eight sections the web
shows is an **append-only array of versioned snapshots**
(:class:`Snapshot`: ``version``, ``body``, ``published_by``, ``published_at``,
``reason``). A snapshot is never updated or deleted; publishing appends the
next one. Cases pin the version in force when they start, so a later publish
never reaches back into an open case.

Between publishes a section has at most one **draft**: the changes
(:class:`~fcrm_contracts.api.policy_config.ConfigDraftChange`) the analyst
has accumulated, each naming one value by ``path``. A draft starts from a
published version (``base_version``) and carries a revision counter; both are
in its ETag, so an edit made against a stale view is a 409, never a silent
overwrite.

Everything here is pure: :class:`SectionSpec` says how a section's body is
read, edited, validated and shown, and :mod:`fcrm_policy.config_sections`
implements it for the eight sections. Storage is
:mod:`fcrm_policy.config_store`, and what a publish does outside the snapshot
log (a new field-set version, a jurisdiction activation, an S2 model) is
:mod:`fcrm_policy.config_effects`.
"""

from __future__ import annotations

import copy
import logging
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, ClassVar, Final, Optional, get_args

from fcrm_common.errors import ValidationError
from fcrm_contracts.api.policy_config import ConfigDraftChange, ConfigSectionKey
from pydantic import BaseModel, ConfigDict

from fcrm_policy.reference import ReferenceStore

logger = logging.getLogger(__name__)

SECTION_KEYS: Final[tuple[ConfigSectionKey, ...]] = get_args(ConfigSectionKey)
SEEDED_NOTE: Final = "Seeded version (P7 fcrm-seed)"
SEEDED_BY: Final = "seed"

Body = dict[str, Any]

_NUMERIC_VERSION: Final = re.compile(r"^v(\d+)$")


class Snapshot(BaseModel):
    """One immutable published version of a section.

    ``seq`` is its 1-based position in the section's history (the section's
    log has exactly one snapshot per ``seq``, which is what serialises two
    concurrent publishes); ``version`` is the label cases pin (``v6``,
    ``v2026-06``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    section: str
    seq: int
    version: str
    body: Body
    published_by: str
    published_at: datetime
    reason: str
    previous_version: Optional[str] = None


class DraftState(BaseModel):
    """A section's working set: the changes made since ``base_version``, the
    version that was in force when the draft began. ``rev`` counts the writes.
    """

    model_config = ConfigDict(extra="forbid")

    section: str
    base_version: str
    rev: int
    changes: list[ConfigDraftChange]
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class SeedSnapshot:
    """The first snapshot of a section, built from the P7 seed."""

    version: str
    body: Body
    published_at: datetime
    published_by: str


@dataclass(frozen=True)
class ViewEnv:
    """What a section's view may need besides its own body: today's date (the
    stale flag of a control), the reference store (the stored jurisdiction
    lists) and the body in force of any other section (the routing page shows
    the boundary distance the scoring model owns).
    """

    today: date
    reference: ReferenceStore
    active_body: Callable[[ConfigSectionKey], Awaitable[Mapping[str, Any]]]


def etag_for(section: str, base_version: str, rev: int) -> str:
    """The ETag of a section's draft state: ``W/"<section>:<base>:<rev>"``.
    Without a draft ``rev`` is 0 and ``base_version`` is the version in force.
    """
    return f'W/"{section}:{base_version}:{rev}"'


def next_numeric_version(current: str) -> str:
    """``v6`` -> ``v7``.

    Raises:
        ValidationError: ``current`` is not ``v<digits>``.
    """
    match = _NUMERIC_VERSION.match(current)
    if match is None:
        raise ValidationError(
            f"{current!r} is not a numbered version, so no next version exists.",
            errors=[{"loc": "section", "msg": "version is not v<number>"}],
        )
    return f"v{int(match.group(1)) + 1}"


def number_text(value: float) -> str:
    """A number as the draft shows it: ``5000000.0`` -> ``5000000``, ``0.36``
    stays ``0.36``.
    """
    return str(int(value)) if float(value).is_integer() else str(value)


def invalid_value(path: str, message: str) -> ValidationError:
    """The 422 for a value that does not suit ``path``; the error names it."""
    text = f"{path}: {message}"
    return ValidationError(text, errors=[{"loc": "body.after", "msg": text}])


def unknown_path(section: str, path: str) -> ValidationError:
    """The 422 for a ``path`` the section has no editable value at."""
    return ValidationError(
        f"{path!r} is not an editable value of {section}.",
        errors=[{"loc": "body.path", "msg": f"{path} is not editable in {section}"}],
    )


def parse_number(
    path: str,
    raw: str,
    *,
    low: float,
    high: float,
    low_open: bool = False,
    integer: bool = False,
) -> float:
    """``raw`` as a number in ``[low, high]`` (``low`` excluded when
    ``low_open``), commas and spaces ignored.

    Raises:
        ValidationError: ``raw`` is not a number, is not whole when ``integer``,
            or is out of range; the error names ``path``.
    """
    text = raw.replace(",", "").replace("_", "").strip()
    try:
        value = float(text)
    except ValueError:
        raise invalid_value(path, f"{raw!r} is not a number.") from None
    if not math.isfinite(value):
        raise invalid_value(path, f"{raw!r} is not a finite number.")
    if integer and not value.is_integer():
        raise invalid_value(path, f"{raw!r} is not a whole number.")
    below = value <= low if low_open else value < low
    if below or value > high:
        bound = f"above {low:g}" if low_open else f"at least {low:g}"
        raise invalid_value(path, f"must be {bound} and at most {high:g}, got {raw!r}.")
    return value


class SectionSpec(ABC):
    """How one section's body is read, edited, checked and shown.

    A body is plain JSON-able data (a ``dict``); a spec never holds state.
    """

    key: ClassVar[ConfigSectionKey]
    title: ClassVar[str]
    owner: ClassVar[str]
    #: ``False`` for the sections whose source of truth is not a value an
    #: analyst can edit here (the policy corpus is ingested, the agent tiers are
    #: files under ``ai/``).
    editable: ClassVar[bool] = True

    @abstractmethod
    def seed(self, raw: Mapping[str, Any]) -> SeedSnapshot:
        """The section's first snapshot from the P7 seed."""

    @abstractmethod
    def value_of(self, body: Mapping[str, Any], path: str) -> str:
        """The value at ``path`` as the draft shows it.

        Raises:
            ValidationError: ``path`` is not an editable value of the section.
        """

    @abstractmethod
    def normalise(self, body: Mapping[str, Any], path: str, after: str) -> str:
        """``after`` as it will be stored, or a 422 naming ``path``.

        Raises:
            ValidationError: ``after`` does not suit ``path``.
        """

    @abstractmethod
    def set_value(self, body: Body, path: str, value: str) -> None:
        """Write the normalised ``value`` at ``path`` into ``body`` (in place)."""

    @abstractmethod
    def next_version(self, current: str, body: Mapping[str, Any]) -> str:
        """The label of the version a publish of ``body`` creates."""

    @abstractmethod
    async def view(self, body: Mapping[str, Any], env: ViewEnv) -> BaseModel:
        """The section's ``data`` as the web renders it."""

    def validate(self, body: Mapping[str, Any]) -> None:
        """Whole-body rules a publish must satisfy (a draft may be partway
        there: edit one weight, then the other).

        Raises:
            ValidationError: a rule is broken; the error names the values.
        """
        return None

    async def check_change(
        self, body: Mapping[str, Any], path: str, after: str, env: ViewEnv
    ) -> None:
        """Rules that need other data than the body (does the target list
        exist); the default has none.
        """
        return None

    async def build_body(
        self, body: Mapping[str, Any], changes: list[ConfigDraftChange], env: ViewEnv
    ) -> Body:
        """The body a publish creates: ``body`` with every change applied.

        Raises:
            ValidationError: a change no longer suits the body.
        """
        new_body: Body = copy.deepcopy(dict(body))
        for change in changes:
            self.set_value(new_body, change.path, change.after)
        return new_body


def apply_change(
    spec: SectionSpec,
    body: Mapping[str, Any],
    changes: list[ConfigDraftChange],
    path: str,
    after: str,
) -> list[ConfigDraftChange]:
    """``changes`` with ``path`` set to ``after``: the stored ``before`` is the
    value the published body has (never the client's), a change back to that
    value drops the entry, and a path already in the draft is replaced in place.

    Raises:
        ValidationError: ``path`` or ``after`` does not suit the section.
    """
    before = spec.value_of(body, path)
    value = spec.normalise(body, path, after)
    rest = [c for c in changes if c.path != path]
    if value == before:
        return rest
    change = ConfigDraftChange(path=path, before=before, after=value)
    index = next((i for i, c in enumerate(changes) if c.path == path), None)
    if index is None:
        return [*rest, change]
    return [*changes[:index], change, *changes[index + 1 :]]
