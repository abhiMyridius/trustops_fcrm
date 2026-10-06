"""Pure checks over a case's audit records and event stream.

The drills fetch the records through the API and hand them here; nothing in
this module does I/O. "Duplicate" means a second record for the same piece of
work, which is what a non-idempotent redelivery would write: the same step
(``data.step_id``), the same agent job and attempt (``data.job_id``), the
same gate or document. A step that legitimately writes several records of one
type tells them apart by one of those keys.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

#: ``data`` keys that, together with the record type, identify one piece of work.
IDENTITY_KEYS: Final[tuple[str, ...]] = (
    "step_id",
    "job_id",
    "attempt",
    "gate_id",
    "doc_id",
)


def sequence_problems(seqs: Sequence[int]) -> list[str]:
    """What is wrong with a chain's ``seq`` numbers: they must be exactly
    ``1..n`` once each, in order.
    """
    if not seqs:
        return []
    problems: list[str] = []
    duplicates = sorted(s for s, n in Counter(seqs).items() if n > 1)
    if duplicates:
        problems.append(f"duplicate seq {duplicates}")
    present = set(seqs)
    gaps = [s for s in range(1, max(seqs) + 1) if s not in present]
    if gaps:
        problems.append(f"gap at seq {gaps}")
    if list(seqs) != sorted(seqs):
        problems.append("records are not in seq order")
    return problems


def record_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """The identity of one record's piece of work: its type plus whichever of
    :data:`IDENTITY_KEYS` its ``data`` carries.
    """
    data = record.get("data")
    data = data if isinstance(data, Mapping) else {}
    return (record.get("type"), *(data.get(key) for key in IDENTITY_KEYS))


def duplicate_problems(
    records: Iterable[Mapping[str, Any]], *, once_types: Iterable[str] = ()
) -> list[str]:
    """Records that exist twice for the same work.

    Args:
        records: The chain's records (``type``, ``seq``, ``data``).
        once_types: Types the case must hold exactly once, however the
            records differ otherwise (``case.created``, ``workflow.started``).

    Returns:
        One sentence per duplicate group; empty when there is none.
    """
    rows = list(records)
    problems: list[str] = []
    wanted = set(once_types)
    by_type: dict[str, list[int]] = {}
    for row in rows:
        by_type.setdefault(str(row.get("type")), []).append(int(row.get("seq", 0)))
    for kind in sorted(wanted):
        seqs = by_type.get(kind, [])
        if len(seqs) > 1:
            problems.append(f"{kind} appears {len(seqs)} times (seq {seqs})")
    groups: dict[tuple[Any, ...], list[int]] = {}
    for row in rows:
        groups.setdefault(record_key(row), []).append(int(row.get("seq", 0)))
    for key, seqs in groups.items():
        has_identity = any(part is not None for part in key[1:])
        if has_identity and len(seqs) > 1 and key[0] not in wanted:
            problems.append(
                f"{key[0]} written {len(seqs)} times for {key[1:]} (seq {seqs})"
            )
    return problems


def chain_problems(
    records: Sequence[Mapping[str, Any]], *, once_types: Iterable[str] = ()
) -> list[str]:
    """Every problem of a chain: sequence and duplicates together."""
    seqs = [int(row.get("seq", 0)) for row in records]
    return [
        *sequence_problems(seqs),
        *duplicate_problems(records, once_types=once_types),
    ]


def stream_problems(connections: Sequence[Sequence[int]], *, head: int) -> list[str]:
    """What is wrong with the ``seq`` numbers one case stream delivered over
    several connections (before and after a restart).

    Within a connection the numbers must rise by exactly one; across them the
    union must run without a gap or a repeat up to ``head``.

    Args:
        connections: The ``seq`` of each frame, per connection, in arrival order.
        head: The last ``seq`` the case's log holds.
    """
    problems: list[str] = []
    combined: list[int] = []
    for number, seqs in enumerate(connections, start=1):
        for previous, current in zip(seqs, seqs[1:], strict=False):
            if current != previous + 1:
                problems.append(
                    f"connection {number}: seq {current} followed {previous}"
                )
        combined.extend(seqs)
    repeated = sorted(s for s, n in Counter(combined).items() if n > 1)
    if repeated:
        problems.append(f"seq delivered twice across connections: {repeated}")
    if combined:
        start = min(combined)
        missing = [s for s in range(start, head + 1) if s not in set(combined)]
        if missing:
            problems.append(
                f"seq never delivered between {start} and {head}: {missing}"
            )
    return problems
