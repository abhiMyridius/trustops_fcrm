"""Exceptions of the drill helpers: one small hierarchy, all specific."""

from __future__ import annotations


class DrillError(RuntimeError):
    """Base of every error the drill helpers raise on purpose."""


class CommandError(DrillError):
    """A ``docker`` (or other) command could not run or exited non-zero."""


class CommandTimeoutError(CommandError):
    """A command ran past its deadline and was killed."""


class PollTimeoutError(DrillError):
    """A condition did not become true before its deadline."""


class StackUnavailableError(DrillError):
    """The stack the drills need is not reachable (containers or gateway)."""


class BrokerAgentError(DrillError):
    """The in-container broker script reported a failure."""


class GatewayError(DrillError):
    """The gateway answered something the drill helpers cannot work with."""
