# ai/policies/tools.rego — agent-to-tool permits (Guard 2).
#
# Source copy: copied with tool_permits.json into ops/opa at build
# (design A §8.2, §12.1 item 7). Queried by the agent-worker (H2):
#   POST {OPA_URL}/v1/data/tools/allow     -> {"result": true|false}
#   POST {OPA_URL}/v1/data/tools/response  -> allow + reason + echo
#
# Input:
#   {"agent": "research", "tool": "policy_search", "case_id": "FCRM-2026-0151",
#    "run_id": "run_...", "role": "fcrm_service",
#    "caller": "model" | "builder",   # optional, default "model"
#    "read_only": true}               # optional; false is always denied
#
# Data: data.tool_permits = {caller_roles[], tools[], permits[{agent, tools[], pack_tools[]}]}.
# tool_permits.json carries the top-level key "tool_permits", so it lands at
# data.tool_permits whether OPA loads the file or its directory; data.tools
# would collide with this package's own path.
#
#   caller "model"   -> the tool must be in the agent's `tools`
#   caller "builder" -> the tool must be in the agent's `pack_tools`
#                       (deterministic context-pack fetches, never exposed
#                       to the model)
#
# Default deny. Only the agent-worker's service identity may call a tool,
# every call is scoped to a case (or "oversight" for the watchlist and
# policy_gap jobs), and no tool call may ask for a write.
package tools

import rego.v1

default allow := false

caller := object.get(input, "caller", "model")

allow if {
	caller_permitted
	case_scoped
	not write_requested
	input.tool in permitted_tools
}

# Tools are only ever called by the agent-worker's service identity; a person
# token can never invoke an agent tool directly.
caller_permitted if input.role in data.tool_permits.caller_roles

# A missing, null or empty case_id is denied.
case_scoped if {
	is_string(input.case_id)
	input.case_id != ""
}

write_requested if input.read_only == false

permitted_tools contains t if {
	caller == "model"
	some permit in data.tool_permits.permits
	permit.agent == input.agent
	some t in permit.tools
}

permitted_tools contains t if {
	caller == "builder"
	some permit in data.tool_permits.permits
	permit.agent == input.agent
	some t in object.get(permit, "pack_tools", [])
}

known_agent if {
	some permit in data.tool_permits.permits
	permit.agent == input.agent
}

known_tool if input.tool in data.tool_permits.tools

known_caller if caller in {"model", "builder"}

default reason := "tool not permitted for this agent"

reason := "permitted" if allow

reason := "caller role may not invoke agent tools" if not caller_permitted

reason := "tool call is not scoped to a case" if {
	caller_permitted
	not case_scoped
}

reason := "tools are read-only" if {
	caller_permitted
	case_scoped
	write_requested
}

reason := "unknown caller kind" if {
	caller_permitted
	case_scoped
	not write_requested
	not known_caller
}

reason := "unknown agent" if {
	caller_permitted
	case_scoped
	not write_requested
	known_caller
	not known_agent
}

reason := "unknown tool" if {
	caller_permitted
	case_scoped
	not write_requested
	known_caller
	known_agent
	not known_tool
}

response := {
	"allow": allow,
	"reason": reason,
	"agent": object.get(input, "agent", null),
	"tool": object.get(input, "tool", null),
	"caller": caller,
	"case_id": object.get(input, "case_id", null),
	"run_id": object.get(input, "run_id", null),
}
