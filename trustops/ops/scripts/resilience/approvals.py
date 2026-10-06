"""What a drill needs approval for, and the switches that give it.

A drill that stops, kills or restarts a container of the stack someone is using
is ``disruptive``; a drill that makes a live AWS call, or restarts the one
process that makes them at start (the api embeds the policy clauses on Bedrock),
is ``aws`` and costs money. Neither runs by default: ``make resilience`` runs the
rest, ``DISRUPTIVE=1`` adds the first kind and ``AWS=1`` the second (which also
needs ``DISRUPTIVE=1``, since each such drill restarts something). The switches
are environment variables so that a person has to type them.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Final, Optional

#: The pytest markers a drill carries and the variable that approves each.
DISRUPTIVE: Final = "disruptive"
AWS: Final = "aws"
ENV_DISRUPTIVE: Final = "FCRM_RESILIENCE_DISRUPTIVE"
ENV_AWS: Final = "FCRM_RESILIENCE_AWS"

#: The only value that approves: a person has to type exactly this, so ``=0`` or
#: ``=no`` can never be mistaken for a yes.
APPROVED: Final = "1"

_RULES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        DISRUPTIVE,
        ENV_DISRUPTIVE,
        "it stops, kills or restarts containers of the running stack",
    ),
    (
        AWS,
        ENV_AWS,
        "it makes live AWS (Bedrock) calls, or restarts the api, which embeds "
        "the policy clauses on Bedrock at start",
    ),
)


def approval_skip_reason(
    markers: Collection[str], environ: Mapping[str, str]
) -> Optional[str]:
    """Why a drill with ``markers`` must not run under ``environ``: the first
    kind of approval it lacks, named with the variable that gives it. ``None``
    when it has every approval it needs (or needs none).
    """
    for marker, variable, what in _RULES:
        if marker in markers and environ.get(variable) != APPROVED:
            return (
                f"needs {variable}=1 (make resilience {_make_switch(marker)}): {what}"
            )
    return None


def _make_switch(marker: str) -> str:
    """The ``make`` variable that sets the approval for ``marker``."""
    return "DISRUPTIVE=1" if marker == DISRUPTIVE else "AWS=1 DISRUPTIVE=1"
