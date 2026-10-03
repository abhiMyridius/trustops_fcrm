# Route authorization tests (design A §8.2 test plan). They run against the
# real ops/opa/data/permissions.json, so a data change that opens a hole
# fails here:
#   opa test ops/opa/policy ops/opa/data ops/opa/tests -v
package authz_test

import rego.v1

import data.authz

req(role, service, method, route) := {
	"role": role,
	"service": service,
	"method": method,
	"route_template": route,
}

# ── allowed ────────────────────────────────────────────────────────────────

test_po_can_raise_a_case if {
	authz.allow with input as req("fcrm_po", "case", "POST", "/cases")
}

test_po_can_confirm_an_extracted_value if {
	authz.allow with input as req("fcrm_po", "case", "POST", "/cases/{id}/intake/values/{field_id}")
}

test_analyst_can_decide_one_review_item if {
	authz.allow with input as req("fcrm_analyst", "case", "POST", "/cases/{id}/items/{item_id}")
}

test_committee_can_vote if {
	authz.allow with input as req("fcrm_committee", "decision", "POST", "/packs/{pack_id}/votes")
}

test_service_can_call_internal_routes if {
	authz.allow with input as req("fcrm_service", "case", "PUT", "/internal/cases/{id}/score")
	authz.allow with input as req("fcrm_service", "case", "PATCH", "/internal/cases/{id}/state")
}

test_service_requests_the_score_of_record if {
	authz.allow with input as req("fcrm_service", "scoring", "POST", "/score")
}

test_service_appends_audit_records if {
	authz.allow with input as req("fcrm_service", "audit", "POST", "/records")
}

test_method_is_case_insensitive if {
	authz.allow with input as req("fcrm_po", "case", "post", "/cases")
}

test_param_names_need_not_match_the_rule if {
	# rule says {id}; FastAPI may name it {case_id}
	authz.allow with input as req("fcrm_po", "case", "GET", "/cases/{case_id}/review")
}

test_concrete_path_matches_a_template_rule if {
	authz.allow with input as req("fcrm_po", "case", "GET", "/cases/FCRM-2026-0151")
}

test_aeo_input_shape_is_accepted if {
	authz.allow with input as {
		"service": "decision",
		"method": "GET",
		"path": "/committee/queue",
		"user": {"id": "u-amendes", "roles": ["fcrm_committee"]},
	}
}

test_wildcard_prefix_with_param if {
	authz.allow with input as req("fcrm_analyst", "audit", "GET", "/records/{case_id}/{seq}")
}

# ── denied ─────────────────────────────────────────────────────────────────

test_po_denied_on_every_scoring_route if {
	not authz.allow with input as req("fcrm_po", "scoring", "POST", "/score")
	not authz.allow with input as req("fcrm_po", "scoring", "GET", "/models")
	not authz.allow with input as req("fcrm_po", "scoring", "GET", "/bands")
}

test_po_denied_on_decision_routes if {
	not authz.allow with input as req("fcrm_po", "decision", "GET", "/packs/{pack_id}")
	not authz.allow with input as req("fcrm_po", "decision", "GET", "/packs/{pack_id}/votes")
}

test_po_denied_review_items if {
	not authz.allow with input as req("fcrm_po", "case", "POST", "/cases/{id}/items/{item_id}")
}

test_po_denied_harness_monitor if {
	not authz.allow with input as req("fcrm_po", "case", "GET", "/monitor/{case_id}")
	not authz.allow with input as req("fcrm_po", "case", "GET", "/monitor/{case_id}/stream")
}

test_analyst_denied_editing_the_submission if {
	# Rule 4: the analyst never edits the submission
	not authz.allow with input as req("fcrm_analyst", "case", "POST", "/cases/{id}/intake/values/{field_id}")
	not authz.allow with input as req("fcrm_analyst", "case", "POST", "/cases/{id}/clarifications/{thread_id}/answer")
}

test_analyst_denied_voting if {
	not authz.allow with input as req("fcrm_analyst", "decision", "POST", "/packs/{pack_id}/votes")
}

test_committee_denied_pack_release if {
	not authz.allow with input as req("fcrm_committee", "decision", "POST", "/packs/{pack_id}/release")
}

test_service_denied_human_gates if {
	# Guard 7: inherits person roles for reads, but never decides a gate
	not authz.allow with input as req("fcrm_service", "decision", "POST", "/packs/{pack_id}/votes")
	not authz.allow with input as req("fcrm_service", "case", "POST", "/cases/{id}/items/{item_id}")
	not authz.allow with input as req("fcrm_service", "case", "POST", "/cases/{id}/submit")
	not authz.allow with input as req("fcrm_service", "case", "POST", "/cases/{id}/acknowledge")
}

test_wrong_method_denied if {
	not authz.allow with input as req("fcrm_po", "case", "DELETE", "/cases/{id}")
	not authz.allow with input as req("fcrm_po", "case", "GET", "/cases/{id}/submit")
	not authz.allow with input as req("fcrm_committee", "decision", "PUT", "/packs/{pack_id}/votes")
}

test_no_bulk_item_route_exists if {
	# Rule 2: there is no batch route to authorise
	not authz.allow with input as req("fcrm_analyst", "case", "POST", "/cases/{id}/items")
}

test_exact_route_beats_template if {
	# GET /cases/search is literal (analyst); PO's GET /cases/{id} must not cover it
	authz.allow with input as req("fcrm_analyst", "case", "GET", "/cases/search")
	not authz.allow with input as req("fcrm_po", "case", "GET", "/cases/search")
}

test_missing_role_denied if {
	not authz.allow with input as {"service": "case", "method": "POST", "route_template": "/cases"}
	authz.response.reason == "no role supplied" with input as {"service": "case", "method": "POST", "route_template": "/cases"}
}

test_unknown_role_denied if {
	not authz.allow with input as req("aeo_admin", "case", "POST", "/cases")
	not authz.allow with input as req("fcrm_admin", "scoring", "POST", "/score")
}

test_unknown_service_denied if {
	not authz.allow with input as req("fcrm_analyst", "ledger", "GET", "/records")
}

test_missing_route_denied if {
	not authz.allow with input as {"role": "fcrm_po", "service": "case", "method": "POST"}
}

test_observer_is_monitor_only if {
	authz.allow with input as req("fcrm_observer", "case", "GET", "/monitor/{case_id}")
	not authz.allow with input as req("fcrm_observer", "case", "GET", "/cases/{id}/workbench")
	not authz.allow with input as req("fcrm_observer", "audit", "GET", "/records")
}

# ── response shape ─────────────────────────────────────────────────────────

test_response_reports_matched_rule if {
	r := authz.response with input as req("fcrm_committee", "decision", "POST", "/packs/{pack_id}/votes")
	r.allow == true
	count(r.matched_rules) == 1
	r.denied_by == []
}

test_response_reports_deny_rule if {
	r := authz.response with input as req("fcrm_service", "decision", "POST", "/packs/{pack_id}/votes")
	r.allow == false
	count(r.denied_by) == 1
	r.reason == "explicitly denied by policy"
}

# ── data hygiene ───────────────────────────────────────────────────────────

test_every_rule_has_a_unique_id if {
	ids := [r.id | some r in data.permissions.rules]
	count(ids) == count({id | some id in ids})
}

test_every_rule_role_is_in_the_hierarchy if {
	every r in data.permissions.rules {
		every role in r.roles {
			data.permissions.role_hierarchy[role]
		}
	}
}

test_every_rule_effect_is_allow_or_deny if {
	every r in data.permissions.rules {
		r.effect in {"allow", "deny"}
	}
}
