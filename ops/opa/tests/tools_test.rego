# Source copy of ai/policies/tools_test.rego, copied into ops/opa at build
# (same provenance as tools.rego and tool_permits.json). Run with:
#   opa test ops/opa/policy ops/opa/data ops/opa/tests -v
package tools_test

import rego.v1

import data.tools

base := {"agent": "research", "tool": "policy_search", "case_id": "FCRM-2026-0151", "run_id": "run_1", "role": "fcrm_service"}

test_research_policy_search_allowed if {
	tools.allow with input as base
}

test_response_carries_reason if {
	r := tools.response with input as base
	r.allow == true
	r.reason == "permitted"
	r.caller == "model"
}

test_unknown_tool_reason if {
	r := tools.response with input as object.union(base, {"tool": "scoring_engine"})
	r.allow == false
	r.reason == "unknown tool"
}

test_tool_not_in_allow_list_denied if {
	not tools.allow with input as object.union(base, {"tool": "audit_log"})
}

test_assessment_has_no_scoring_tool if {
	not tools.allow with input as object.union(base, {"agent": "assessment", "tool": "scoring_engine"})
}

test_unknown_agent_denied if {
	not tools.allow with input as object.union(base, {"agent": "orchestrator"})
}

test_person_role_denied if {
	not tools.allow with input as object.union(base, {"role": "fcrm_analyst"})
	r := tools.response with input as object.union(base, {"role": "fcrm_po"})
	r.reason == "caller role may not invoke agent tools"
}

test_null_case_denied if {
	not tools.allow with input as object.union(base, {"case_id": null})
}

test_missing_case_denied if {
	not tools.allow with input as object.remove(base, ["case_id"])
}

test_write_denied if {
	not tools.allow with input as object.union(base, {"read_only": false})
	r := tools.response with input as object.union(base, {"read_only": false})
	r.reason == "tools are read-only"
}

test_no_permit_grants_a_scoring_tool if {
	every p in data.tool_permits.permits {
		every t in array.concat(p.tools, object.get(p, "pack_tools", [])) {
			not startswith(t, "scoring")
		}
	}
}

test_every_permitted_tool_is_registered if {
	every p in data.tool_permits.permits {
		every t in array.concat(p.tools, object.get(p, "pack_tools", [])) {
			t in data.tool_permits.tools
		}
	}
}

test_builder_only_tool_denied_to_model if {
	not tools.allow with input as object.union(base, {"tool": "submission_diff"})
}

test_builder_only_tool_allowed_to_builder if {
	tools.allow with input as object.union(base, {"tool": "submission_diff", "caller": "builder"})
}

test_oversight_scope_allowed if {
	tools.allow with input as {"agent": "watchlist", "tool": "case_search", "case_id": "oversight", "run_id": "run_2", "role": "fcrm_service"}
}

test_every_listed_model_tool_allowed if {
	every p in data.tool_permits.permits {
		every t in p.tools {
			tools.allow with input as {"agent": p.agent, "tool": t, "case_id": "FCRM-2026-0151", "role": "fcrm_service"}
		}
	}
}
