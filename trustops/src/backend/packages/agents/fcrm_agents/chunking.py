"""Splitting one job's input into parts (design B §2.2, §3.3; ADR-77).

A pack that does not fit (a section the agent reads in full is over its cap,
or the whole pack is over its tier total) used to be cut, or refused with
:class:`~fcrm_agents.context_pack.PackOverflowError`. Instead, H2 can run the
agent once per **part**: each part is a whole pack of its own that repeats the
small sections every answer needs and shows a share of the large ones, and the
parts' answers are merged by code (:mod:`fcrm_agents.consolidate`).

What is split
-------------
:data:`SPLIT_SPECS` names, per agent, the **sharded** sections (split between
the parts; every item in exactly one part) and the **replicated** ones (shown
whole, as fitted, in every part). Any other section (the identity, versions
and re-run prelude, the examiner's question) is replicated too.

How the parts are filled
------------------------
:func:`plan_parts` fills parts greedily, in the draft's order, by **group**:
the group a builder named for an item (a risk area, a domain, a country, a
gap), so related items travel together. With ``group_sections`` the sharded
sections are split together by group (assessment: a domain's controls, their
evidence and their precedent ratings in one part). A part takes a group when
every sharded section stays under its section cap and the whole part under the
tier total. A group that does not fit alone is split by item; in grouped
sections an item travels with the items that name it (an evidence line, or a
``case/control`` rating, goes with its control: an id is matched on its
``/``-separated segments). A single item over its cap alone is shown alone,
with the section's usual note, or raises for a never-truncated section, as an
unsplit pack would.

A pack that fits is not split: :func:`plan_parts` then returns one plan marked
``whole``, and the builder returns the very pack ``build`` makes (same text,
same hash). More parts than ``max_parts`` raises :class:`TooManyPartsError`.

What a part says
----------------
Each part carries a ``part`` prelude line (after identity, versions and
re-run) naming the items it shows; each sharded section starts with a head
note ``[part i of n: items a–b of total]`` (never "omitted by budget": the
other items are not dropped, another part shows them). No prompt changes: the
model reads ``ContextPack.user_text()`` as before.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Optional

from fcrm_contracts.enums import AgentName
from pydantic import BaseModel, ConfigDict, Field

from fcrm_agents.context_pack import (
    CHARS_PER_TOKEN,
    NEVER_TRUNCATE,
    PRELUDE_TOKEN_CAP,
    PackDraft,
    PackOverflowError,
    PackSection,
    estimate_tokens,
    fit_items,
    render_user_text,
)

logger = logging.getLogger(__name__)

PART_SECTION: Final = "part"
LEADING_PRELUDE: Final[frozenset[str]] = frozenset({"identity", "versions", "rerun"})
"""The sections the ``part`` line follows."""

Slot = tuple[str, int]
"""``(section name, item index)``: one item of the draft."""


class TooManyPartsError(PackOverflowError):
    """The input needs more parts than a job may run (``AGENT_MAX_PARTS``)."""

    def __init__(self, agent: AgentName, *, needed: int, allowed: int) -> None:
        """Record how many parts were ``needed`` and how many are ``allowed``."""
        super().__init__(
            f"{agent.value} input needs {needed} parts; at most {allowed} allowed"
        )
        self.needed = needed
        self.allowed = allowed


class SplitSpec(BaseModel):
    """How one agent's input splits into parts (module doc)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sharded: tuple[str, ...]
    replicated: tuple[str, ...] = ()
    group_sections: bool = False
    """Split the sharded sections together, by the groups their items share."""


SPLIT_SPECS: Final[Mapping[AgentName, SplitSpec]] = {
    AgentName.SCREENING: SplitSpec(sharded=("precedents",), replicated=("fields",)),
    AgentName.INTAKE: SplitSpec(sharded=("document",), replicated=("field_set",)),
    AgentName.RESEARCH: SplitSpec(
        sharded=("clauses", "precedents"),
        replicated=("fields", "jurisdictions", "controls"),
    ),
    AgentName.ASSESSMENT: SplitSpec(
        sharded=("controls", "evidence", "precedent_ratings"),
        replicated=("fields", "clauses_brief"),
        group_sections=True,
    ),
    AgentName.CRITIC: SplitSpec(
        sharded=("proposals",),
        replicated=("score", "precedent_ratings", "rules", "evidence_dates"),
    ),
    AgentName.NARRATIVE: SplitSpec(
        sharded=("findings", "gaps", "answers", "evidence_dates", "clauses", "planned"),
        replicated=("fields", "ratings", "owner_facts"),
    ),
    AgentName.EXAMINER: SplitSpec(
        sharded=("records",), replicated=("question", "routing")
    ),
    AgentName.WATCHLIST: SplitSpec(
        sharded=("candidates",), replicated=("list_diff", "clauses")
    ),
    AgentName.POLICY_GAP: SplitSpec(sharded=("gaps", "nearest"), group_sections=True),
}
"""Per agent: the sections split between parts and those every part repeats."""


class PartPlan(BaseModel):
    """One part: which items of each sharded section it shows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=1)
    count: int = Field(ge=1)
    shown: dict[str, tuple[int, ...]] = Field(default_factory=dict)
    """Per sharded section, the indexes (0-based, in the draft's order) of the
    items this part shows."""
    whole: bool = False
    """The pack fits as built: one part, nothing split (``build``'s pack)."""


def _ranges(indexes: Sequence[int]) -> str:
    """1-based item numbers as ranges: ``1–3, 7, 9–10``."""
    numbers = sorted(i + 1 for i in indexes)
    spans: list[list[int]] = []
    for n in numbers:
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ", ".join(f"{a}–{b}" if a != b else str(a) for a, b in spans)


def _shows(indexes: Sequence[int], total: int) -> str:
    if not indexes:
        return f"none of {total} items"
    noun = "item" if len(indexes) == 1 else "items"
    return f"{noun} {_ranges(indexes)} of {total}"


def section_head(index: int, count: int, indexes: Sequence[int], total: int) -> str:
    """The note a sharded section of a part starts with."""
    return f"[part {index} of {count}: {_shows(indexes, total)}]"


def part_line(
    index: int,
    count: int,
    shown: Mapping[str, Sequence[int]],
    totals: Mapping[str, int],
) -> str:
    """The ``part`` prelude line: what this part shows and how it is answered."""
    held = "; ".join(
        f"{name}: {_shows(indexes, totals[name])}" for name, indexes in shown.items()
    )
    return (
        f"Part {index} of {count} of this job's input: {held}. The other parts "
        "are answered separately and the answers are merged by code. Answer only "
        "from what is shown here."
    )


def _sharded_names(agent: AgentName, draft: PackDraft) -> list[str]:
    spec = SPLIT_SPECS[agent]
    return [s.name for s in draft.sections if s.name in spec.sharded]


def _sharded_section(
    draft: PackDraft, name: str, indexes: Sequence[int], head: str
) -> PackSection:
    """A sharded section as a part shows it: the head note, then its items (a
    lone item over the cap gets the section's usual note, or raises)."""
    rows = draft.items[name]
    cap = draft.cap_for(name)
    items = [rows[i] for i in sorted(indexes)]
    body = "\n".join(line for _, line in items)
    truncated = False
    if items and len(head) + 1 + len(body) > cap * CHARS_PER_TOKEN:
        fitted = fit_items(
            name,
            items,
            cap,
            never_truncate=name in NEVER_TRUNCATE,
            note=draft.notes[name],
        )
        body, truncated = fitted.text, fitted.truncated
    text = f"{head}\n{body}" if items else head
    return PackSection(
        name=name,
        text=text,
        cap_tokens=cap,
        cap_chars=cap * CHARS_PER_TOKEN,
        tokens=estimate_tokens(text),
        truncated=truncated,
        items_total=len(rows),
        items_shown=len(items),
    )


def _line_section(name: str, text: str) -> PackSection:
    cap = max(PRELUDE_TOKEN_CAP, math.ceil(len(text) / CHARS_PER_TOKEN))
    return PackSection(
        name=name,
        text=text,
        cap_tokens=cap,
        cap_chars=cap * CHARS_PER_TOKEN,
        tokens=estimate_tokens(text),
        truncated=False,
        items_total=1,
        items_shown=1,
    )


def render_part(
    agent: AgentName, draft: PackDraft, plan: PartPlan
) -> tuple[list[PackSection], dict[str, set[str]], dict[str, tuple[int, int, int]]]:
    """Part ``plan`` of ``draft``: its sections in pack order (the ``part`` line
    after the leading prelude), the item ids it shows per section (what its
    sources are filtered by) and, per sharded section, ``(first, last, total)``.

    Raises:
        PackOverflowError: a lone never-truncated item is over its cap.
    """
    sharded = _sharded_names(agent, draft)
    totals = {name: len(draft.items[name]) for name in sharded}
    shown_idx = {name: plan.shown.get(name, ()) for name in sharded}
    sections: list[PackSection] = []
    shown: dict[str, set[str]] = {}
    part_items: dict[str, tuple[int, int, int]] = {}
    for section in draft.sections:
        name = section.name
        if PART_SECTION not in shown and name not in LEADING_PRELUDE:
            line = part_line(plan.index, plan.count, shown_idx, totals)
            sections.append(_line_section(PART_SECTION, line))
            shown[PART_SECTION] = set()
        if name in shown_idx:
            indexes = sorted(shown_idx[name])
            head = (
                f"[part {plan.index} of {plan.count}: {_shows(indexes, totals[name])}]"
            )
            sections.append(_sharded_section(draft, name, indexes, head))
            shown[name] = {draft.items[name][i][0] for i in indexes}
            if indexes:
                part_items[name] = (indexes[0] + 1, indexes[-1] + 1, totals[name])
        else:
            sections.append(section)
            shown[name] = {i for i, _ in draft.items[name][: section.items_shown]}
    return sections, shown, part_items


def _fits_whole(draft: PackDraft, sharded: Sequence[str], token_cap: int) -> bool:
    if draft.overflows or any(s.truncated for s in draft.sections if s.name in sharded):
        return False
    text = render_user_text({s.name: s.text for s in draft.sections})
    return estimate_tokens(text) <= token_cap


def _replicated_overflow(draft: PackDraft, sharded: Sequence[str]) -> None:
    """Raise the overflow of a section no split can shrink."""
    for name, error in draft.overflows.items():
        if name not in sharded:
            raise error


def _group_key(draft: PackDraft, name: str, index: int, joint: bool) -> object:
    groups = draft.groups.get(name)
    group = groups[index] if groups is not None else f"{name}#{index}"
    return group if joint else (name, group)


def _top_units(
    agent: AgentName, draft: PackDraft, sharded: Sequence[str]
) -> list[list[Slot]]:
    """The groups to place, in order of first appearance."""
    joint = SPLIT_SPECS[agent].group_sections
    units: dict[object, list[Slot]] = {}
    for name in sharded:
        for index in range(len(draft.items[name])):
            units.setdefault(_group_key(draft, name, index, joint), []).append(
                (name, index)
            )
    return list(units.values())


def _anchor_units(draft: PackDraft, unit: Sequence[Slot]) -> list[list[Slot]]:
    """A grouped unit split by the first section's items, each with the items
    of the other sections that name it (module doc)."""
    anchor = unit[0][0]
    taken: set[Slot] = set()
    subs: list[list[Slot]] = []
    for slot in unit:
        if slot[0] != anchor:
            continue
        anchor_id = draft.items[anchor][slot[1]][0]
        sub = [slot] + [
            other
            for other in unit
            if other[0] != anchor
            and other not in taken
            and anchor_id in draft.items[other[0]][other[1]][0].split("/")
        ]
        taken.update(sub)
        subs.append(sub)
    subs += [[slot] for slot in unit if slot not in taken and slot[0] != anchor]
    return subs


class _Planner:
    """The greedy fill (module doc)."""

    def __init__(
        self,
        agent: AgentName,
        draft: PackDraft,
        *,
        token_cap: int,
        max_parts: int,
    ) -> None:
        self.agent = agent
        self.draft = draft
        self.sharded = _sharded_names(agent, draft)
        self.joint = SPLIT_SPECS[agent].group_sections
        self.token_cap = token_cap
        self.max_parts = max_parts
        self.parts: list[list[Slot]] = []
        self.current: list[Slot] = []

    def _plan(self, slots: Iterable[Slot], index: int, count: int) -> PartPlan:
        shown: dict[str, list[int]] = {name: [] for name in self.sharded}
        for name, item in slots:
            shown[name].append(item)
        return PartPlan(
            index=index,
            count=count,
            shown={name: tuple(sorted(items)) for name, items in shown.items()},
        )

    def tokens(self, slots: Sequence[Slot]) -> int:
        """The estimate of a part holding ``slots`` (numbered as the last of
        ``max_parts``, the longest its part line can be)."""
        plan = self._plan(slots, self.max_parts, self.max_parts)
        sections, _, _ = render_part(self.agent, self.draft, plan)
        return estimate_tokens(render_user_text({s.name: s.text for s in sections}))

    def fits(self, slots: Sequence[Slot]) -> bool:
        """Every sharded section under its cap and the part under the total."""
        for name in self.sharded:
            items = [i for n, i in slots if n == name]
            if not items:
                continue
            rows = self.draft.items[name]
            body = "\n".join(rows[i][1] for i in sorted(items))
            head = section_head(self.max_parts, self.max_parts, items, len(rows))
            if len(head) + 1 + len(body) > self.draft.cap_for(name) * CHARS_PER_TOKEN:
                return False
        return self.tokens(slots) <= self.token_cap

    def close(self) -> None:
        """End the part being filled."""
        if self.current:
            self.parts.append(self.current)
            self.current = []

    def _split(self, unit: Sequence[Slot], level: int) -> Optional[list[list[Slot]]]:
        if len(unit) == 1:
            return None
        if level == 0 and self.joint:
            subs = _anchor_units(self.draft, unit)
            if len(subs) > 1:
                return subs
        return [[slot] for slot in unit]

    def place(self, unit: Sequence[Slot], level: int = 0) -> None:
        """Add ``unit`` to the current part, a new part, or split it."""
        if self.fits([*self.current, *unit]):
            self.current.extend(unit)
            return
        self.close()
        if self.fits(unit):
            self.current.extend(unit)
            return
        subs = self._split(unit, level)
        if subs is None:
            self._alone(unit)
            return
        for sub in subs:
            self.place(sub, level + 1)

    def _alone(self, unit: Sequence[Slot]) -> None:
        """A single item over its cap: shown alone with the section's note (or
        raising for a never-truncated section, from :func:`render_part`)."""
        tokens = self.tokens(unit)
        if tokens > self.token_cap:
            name, index = unit[0]
            item_id = self.draft.items[name][index][0]
            msg = (
                f"{self.agent.value} {name} item {item_id} alone makes a part of "
                f"~{tokens} tokens; tier cap {self.token_cap}"
            )
            raise PackOverflowError(msg)
        self.current.extend(unit)
        self.close()

    def plans(self) -> list[PartPlan]:
        """The parts, numbered."""
        self.close()
        count = len(self.parts)
        return [self._plan(slots, i, count) for i, slots in enumerate(self.parts, 1)]


def plan_parts(
    agent: AgentName, draft: PackDraft, *, token_cap: int, max_parts: int
) -> list[PartPlan]:
    """Lay ``draft`` out over parts (module doc).

    Returns one ``whole`` plan when the pack fits as built; otherwise the parts,
    every sharded item in exactly one of them.

    Raises:
        TooManyPartsError: more than ``max_parts`` parts are needed.
        PackOverflowError: a section that is not split overflowed, the
            sections every part repeats leave no room, or a single item is over
            its cap or the tier total.
    """
    sharded = _sharded_names(agent, draft)
    _replicated_overflow(draft, sharded)
    if _fits_whole(draft, sharded, token_cap):
        return [PartPlan(index=1, count=1, whole=True)]
    if not sharded:
        text = render_user_text({s.name: s.text for s in draft.sections})
        msg = (
            f"{agent.value} pack is ~{estimate_tokens(text)} tokens; tier cap "
            f"{token_cap}"
        )
        raise PackOverflowError(msg)
    planner = _Planner(agent, draft, token_cap=token_cap, max_parts=max_parts)
    for unit in _top_units(agent, draft, sharded):
        planner.place(unit)
    plans = planner.plans()
    if len(plans) > max_parts:
        raise TooManyPartsError(agent, needed=len(plans), allowed=max_parts)
    logger.debug(
        "input split into parts", extra={"agent": agent.value, "parts": len(plans)}
    )
    return plans
