"""Exceptions raised by tool clients. A tool error is something the model
should see as a tool failure (design B §4.3: "the agent receives a tool
error ... and must proceed without it"), so they share one base class the
runtime turns into a tool-error string; programming errors are not caught.
"""

from __future__ import annotations


class ToolError(Exception):
    """Base class for every tool-client refusal."""


class ToolInputError(ToolError):
    """The request is the wrong model or carries invalid values."""


class UnknownEntityError(ToolError, LookupError):
    """The case, document, control, version or list asked for does not exist."""


class ToolScopeError(ToolError):
    """The request is outside the job's scope: another case id, or a pinned
    version (corpus, controls, field set) other than the case's (design B
    §1.3 ``scope_rule``, §3.5)."""


class ToolNotPermittedError(ToolError):
    """The tool is not in the caller's allow-list (Python mirror of OPA)."""


class UnknownToolError(ToolError, LookupError):
    """No tool with that name is registered."""
