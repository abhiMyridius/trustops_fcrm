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

test_roster_is_readable_by_analyst_committee_and_service if {
	# GET /auth/users?role= (R5 auto-triage, committee release): the route
	# template is "/users" relative to X1's mount.
	authz.allow with input as req("fcrm_analyst", "identity", "GET", "/users")
	authz.allow with input as req("fcrm_committee", "identity", "GET", "/users")
	authz.allow with input as req("fcrm_service", "identity", "GET", "/users")
}

test_roster_is_not_readable_by_po_or_observer if {
	not authz.allow with input as req("fcrm_po", "identity", "GET", "/users")
	not authz.allow with input as req("fcrm_observer", "identity", "GET", "/users")
}

test_own_profile_is_readable_by_every_person_role if {
	authz.allow with input as req("fcrm_po", "identity", "GET", "/me")
	authz.allow with input as req("fcrm_observer", "identity", "GET", "/me")
	authz.allow with input as req("fcrm_po", "identity", "GET", "/users/me")
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

# ── S2-S5 route matrix (the routes fcrm_*/routes.py actually serve) ────────
# Mirrors the require_roles() lists in each service's routes.py; both layers
# must agree, so a change to one without the other fails here.

test_scoring_matrix if {
	# POST /score: service and analyst; never committee, never PO
	authz.allow with input as req("fcrm_analyst", "scoring", "POST", "/score")
	authz.allow with input as req("fcrm_service", "scoring", "POST", "/score")
	not authz.allow with input as req("fcrm_committee", "scoring", "POST", "/score")
	not authz.allow with input as req("fcrm_po", "scoring", "POST", "/score")
	# models and routing: analyst, committee, service; never PO
	authz.allow with input as req("fcrm_committee", "scoring", "GET", "/models/active")
	authz.allow with input as req("fcrm_committee", "scoring", "GET", "/models/{version}")
	authz.allow with input as req("fcrm_committee", "scoring", "GET", "/routing/active")
	authz.allow with input as req("fcrm_analyst", "scoring", "GET", "/routing/active")
	authz.allow with input as req("fcrm_service", "scoring", "GET", "/routing/active")
	not authz.allow with input as req("fcrm_po", "scoring", "GET", "/models/active")
	not authz.allow with input as req("fcrm_po", "scoring", "GET", "/routing/active")
	not authz.allow with input as req("fcrm_observer", "scoring", "GET", "/models/active")
}

test_policy_search_matrix if {
	authz.allow with input as req("fcrm_analyst", "policy", "POST", "/search")
	authz.allow with input as req("fcrm_committee", "policy", "POST", "/search")
	authz.allow with input as req("fcrm_service", "policy", "POST", "/search")
	not authz.allow with input as req("fcrm_po", "policy", "POST", "/search")
	not authz.allow with input as req("fcrm_observer", "policy", "POST", "/search")
}

test_audit_matrix if {
	# POST /records: the service account only
	authz.allow with input as req("fcrm_service", "audit", "POST", "/records")
	not authz.allow with input as req("fcrm_analyst", "audit", "POST", "/records")
	not authz.allow with input as req("fcrm_committee", "audit", "POST", "/records")
	not authz.allow with input as req("fcrm_po", "audit", "POST", "/records")
	# reads: analyst, committee, service; never PO (record summaries carry score text)
	authz.allow with input as req("fcrm_analyst", "audit", "GET", "/verify/{case_id}")
	authz.allow with input as req("fcrm_committee", "audit", "GET", "/verify/{case_id}")
	authz.allow with input as req("fcrm_service", "audit", "GET", "/verify/{case_id}")
	authz.allow with input as req("fcrm_analyst", "audit", "GET", "/records/{case_id}")
	authz.allow with input as req("fcrm_committee", "audit", "GET", "/records/{case_id}")
	authz.allow with input as req("fcrm_service", "audit", "GET", "/records/{case_id}")
	not authz.allow with input as req("fcrm_po", "audit", "GET", "/verify/{case_id}")
	not authz.allow with input as req("fcrm_po", "audit", "GET", "/records/{case_id}")
	not authz.allow with input as req("fcrm_observer", "audit", "GET", "/records/{case_id}")
}

test_decision_matrix if {
	# POST /packs: the service account only (H1 freezes the pack)
	authz.allow with input as req("fcrm_service", "decision", "POST", "/packs")
	not authz.allow with input as req("fcrm_analyst", "decision", "POST", "/packs")
	not authz.allow with input as req("fcrm_committee", "decision", "POST", "/packs")
	not authz.allow with input as req("fcrm_po", "decision", "POST", "/packs")
	# votes: committee only (Guard 7 denies the service account)
	authz.allow with input as req("fcrm_committee", "decision", "POST", "/packs/{pack_id}/votes")
	not authz.allow with input as req("fcrm_analyst", "decision", "POST", "/packs/{pack_id}/votes")
	not authz.allow with input as req("fcrm_service", "decision", "POST", "/packs/{pack_id}/votes")
	# questions: committee only; answers: analyst or service
	authz.allow with input as req("fcrm_committee", "decision", "POST", "/packs/{pack_id}/questions")
	not authz.allow with input as req("fcrm_analyst", "decision", "POST", "/packs/{pack_id}/questions")
	not authz.allow with input as req("fcrm_service", "decision", "POST", "/packs/{pack_id}/questions")
	authz.allow with input as req("fcrm_analyst", "decision", "POST", "/questions/{question_id}/answers")
	authz.allow with input as req("fcrm_service", "decision", "POST", "/questions/{question_id}/answers")
	not authz.allow with input as req("fcrm_committee", "decision", "POST", "/questions/{question_id}/answers")
	# reads: analyst, committee, service; never PO
	authz.allow with input as req("fcrm_analyst", "decision", "GET", "/packs/{pack_id}/outcome")
	authz.allow with input as req("fcrm_committee", "decision", "GET", "/packs/{pack_id}/outcome")
	authz.allow with input as req("fcrm_service", "decision", "GET", "/packs/{pack_id}/outcome")
	authz.allow with input as req("fcrm_service", "decision", "GET", "/packs/{pack_id}")
	authz.allow with input as req("fcrm_service", "decision", "GET", "/packs/{pack_id}/votes")
	not authz.allow with input as req("fcrm_po", "decision", "GET", "/packs/{pack_id}/outcome")
	# release checks: analyst and service
	authz.allow with input as req("fcrm_analyst", "decision", "POST", "/release-checks")
	authz.allow with input as req("fcrm_service", "decision", "POST", "/release-checks")
	not authz.allow with input as req("fcrm_committee", "decision", "POST", "/release-checks")
	not authz.allow with input as req("fcrm_po", "decision", "POST", "/release-checks")
}

test_po_denied_on_every_s2_to_s5_route if {
	every r in [
		["scoring", "POST", "/score"],
		["scoring", "GET", "/models/active"],
		["scoring", "GET", "/models/{version}"],
		["scoring", "GET", "/routing/active"],
		["policy", "POST", "/search"],
		["audit", "POST", "/records"],
		["audit", "GET", "/verify/{case_id}"],
		["audit", "GET", "/records/{case_id}"],
		["decision", "POST", "/release-checks"],
		["decision", "POST", "/packs"],
		["decision", "GET", "/packs/{pack_id}"],
		["decision", "POST", "/packs/{pack_id}/votes"],
		["decision", "GET", "/packs/{pack_id}/votes"],
		["decision", "GET", "/packs/{pack_id}/outcome"],
		["decision", "POST", "/packs/{pack_id}/questions"],
		["decision", "POST", "/questions/{question_id}/answers"],
	] {
		not authz.allow with input as req("fcrm_po", r[0], r[1], r[2])
	}
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
