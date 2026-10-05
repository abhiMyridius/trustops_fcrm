"""The shape of the OPA client Guard 2 expects. Protocol only.

The real client belongs to ``fcrm_common`` (``fcrm_common.authz``: OPA over
HTTP with the 30-60 s Redis decision cache, BUILD-PLAN §3.7, design B
§4.3). This package must not depend on ``fcrm_common`` or ``httpx``, so it
states the one method it calls and the real client satisfies it
structurally. Keep the two in step: this Protocol is the contract.

``tool_permit`` evaluates ``data.tools.allow`` (``ops/opa/tools.rego`` over
``data.tool_permits``) with the input
``{"agent", "tool", "case_id", "role", "run_id"}`` (design B §4.3 adds
``read_only: true``, which the real client sets itself). It returns
``True`` only for an explicit allow. Guard 2 treats anything else, including
any exception or a timeout, as a denial: there is no default-allow path.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class OPAClient(Protocol):
    """Agent-to-tool permit decisions."""

    async def tool_permit(
        self, *, agent: str, tool: str, case_id: str, role: str, run_id: str
    ) -> bool:
        """``True`` when OPA explicitly allows ``agent`` to call ``tool``."""
        ...
