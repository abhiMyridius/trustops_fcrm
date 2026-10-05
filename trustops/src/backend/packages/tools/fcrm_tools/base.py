"""The tool interface and the registry (design B §1.3; BUILD-PLAN §3.4).

Every tool is read-only, takes one ``fcrm_contracts.tools`` input model and
returns its output model. :class:`Tool` is the structural contract every
implementation satisfies (the seed-backed ones here; the HTTP clients
against S1/S3/S4 once ``fcrm_common`` lands); :class:`BaseTool` is the
convenience base that enforces the input type.

Permit keys (Guard 2)
---------------------
**Convention chosen here: the permit key is the bare tool name**
(``"policy_search"``). Rationale: the OPA input is
``{"agent", "tool", "case_id", "role", "run_id"}`` (``fcrm_guardrails.
opa_client``), so the *pair* is formed by the policy from two fields; a tool
needs exactly one key, independent of which agent calls it, and
``PermitGuard.decide(tool=...)`` already passes the bare name. Design B
§1.3's tool YAML shows ``permit_key: tools.policy_search`` and the brief
mentions ``agent:tool`` pairs; :func:`permit_pair` gives the pair form for
decision-cache keys. Reconcile with the ai/ and ops/OPA lanes (one line to
change: :func:`permit_key_for`).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import (
    Final,
    Generic,
    Optional,
    Protocol,
    TypeVar,
    Union,
    cast,
    runtime_checkable,
)

from fcrm_prompts import PromptLibrary
from pydantic import BaseModel, ConfigDict, Field

from fcrm_tools.errors import ToolInputError, UnknownToolError

logger = logging.getLogger(__name__)

TOOL_NAMES: Final[tuple[str, ...]] = (
    "document_reader",
    "case_record",
    "case_search",
    "field_rules",
    "policy_search",
    "precedent_search",
    "control_inventory",
    "evidence_store",
    "jurisdiction_list",
    "rules_check",
    "evidence_ledger",
    "audit_log",
    "submission_diff",
)
"""The thirteen read-only tools (BUILD-PLAN §3.4), one per
``fcrm_contracts.tools`` schema."""


def permit_key_for(tool_name: str) -> str:
    """The OPA permit key of a tool: the bare tool name (see module doc)."""
    return tool_name


def permit_pair(agent: str, tool_name: str) -> str:
    """``"<agent>:<tool>"``: the pair OPA decides on, for cache keys/logs."""
    return f"{agent}:{tool_name}"


@runtime_checkable
class Tool(Protocol):
    """A read-only agent tool."""

    @property
    def name(self) -> str:
        """The tool name agents declare (one of :data:`TOOL_NAMES`)."""
        ...

    @property
    def permit_key(self) -> str:
        """The key Guard 2 asks OPA about."""
        ...

    @property
    def input_model(self) -> type[BaseModel]:
        """The ``fcrm_contracts.tools`` input model."""
        ...

    @property
    def output_model(self) -> type[BaseModel]:
        """The ``fcrm_contracts.tools`` output model."""
        ...

    async def call(self, input: BaseModel) -> BaseModel:
        """Run the tool for one validated request."""
        ...


InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=BaseModel)


class BaseTool(ABC, Generic[InT, OutT]):
    """Base for concrete tools: checks the request type, then :meth:`run`.

    Subclasses set ``name``, ``input_model`` and ``output_model`` as class
    attributes and implement :meth:`run`.
    """

    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]

    @property
    def permit_key(self) -> str:
        """The bare tool name (module doc, "Permit keys")."""
        return permit_key_for(self.name)

    async def call(self, input: BaseModel) -> BaseModel:
        """Validate the request type and run the tool.

        Raises:
            ToolInputError: ``input`` is not this tool's input model.
        """
        if not isinstance(input, self.input_model):
            msg = (
                f"{self.name} expects {self.input_model.__name__}, "
                f"got {type(input).__name__}"
            )
            raise ToolInputError(msg)
        request = cast(InT, input)  # narrowed by the isinstance check above
        return await self.run(request)

    @abstractmethod
    async def run(self, request: InT) -> OutT:
        """Produce the output for one request."""


class SpecCheckReport(BaseModel):
    """Registry vs. declared agent tool lists (``ai/agents/*.agent.yaml``)."""

    model_config = ConfigDict(extra="forbid")

    skipped: bool = False
    reason: Optional[str] = None
    agents_checked: list[str] = Field(default_factory=list)
    missing: dict[str, list[str]] = Field(default_factory=dict)
    undeclared: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every declared tool is registered (or the check was
        skipped because there was nothing to check against)."""
        return not self.missing


class ToolRegistry:
    """Tool name -> tool instance."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        """Register ``tools`` (in order)."""
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Add ``tool``.

        Raises:
            ValueError: a tool with the same name is already registered.
        """
        if tool.name in self._tools:
            msg = f"tool {tool.name!r} is already registered"
            raise ValueError(msg)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """The tool called ``name``.

        Raises:
            UnknownToolError: nothing registered under ``name``.
        """
        try:
            return self._tools[name]
        except KeyError as exc:
            msg = f"no tool {name!r} registered"
            raise UnknownToolError(msg) from exc

    def names(self) -> tuple[str, ...]:
        """Registered tool names, in registration order."""
        return tuple(self._tools)

    def permit_keys(self) -> dict[str, str]:
        """``{tool name: permit key}``."""
        return {name: tool.permit_key for name, tool in self._tools.items()}

    def subset(self, names: Iterable[str]) -> ToolRegistry:
        """A registry holding only ``names`` (each must be registered)."""
        return ToolRegistry(self.get(name) for name in names)

    def __contains__(self, name: object) -> bool:
        """True when a tool called ``name`` is registered."""
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        """Registered tools, in registration order."""
        return iter(self._tools.values())

    def __len__(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    def check_declared(self, declared: Mapping[str, Sequence[str]]) -> SpecCheckReport:
        """Compare ``{agent: [tool, ...]}`` with what is registered."""
        missing = {
            agent: sorted(set(tools) - set(self._tools))
            for agent, tools in sorted(declared.items())
            if set(tools) - set(self._tools)
        }
        used = {tool for tools in declared.values() for tool in tools}
        return SpecCheckReport(
            agents_checked=sorted(declared),
            missing=missing,
            undeclared=sorted(set(self._tools) - used),
        )

    def check_agent_specs(
        self, ai_root: Optional[Union[str, Path]] = None
    ) -> SpecCheckReport:
        """Check the registry against ``<ai_root>/agents/*.agent.yaml``.

        When the ai/ lane has not landed (no root, or no agent specs) the
        check is **skipped** with a warning rather than failing: there is
        nothing to compare against yet, and a later lock test makes this
        strict.
        """
        library = PromptLibrary(ai_root)
        specs = library.agent_specs
        if not specs:
            reason = f"no agent specs under {library.root}; check skipped"
            logger.warning(reason)
            return SpecCheckReport(skipped=True, reason=reason)
        report = self.check_declared({name: s.tools for name, s in specs.items()})
        if not report.ok:
            logger.error("agent specs name unregistered tools: %s", report.missing)
        return report
