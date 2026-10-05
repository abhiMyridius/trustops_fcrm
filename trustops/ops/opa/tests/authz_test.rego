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

# S1's internal routes (design A section 8.2: /internal/* is service-only). The set is
# what fcrm_case.routes_internal serves; the gateway also 404s any /internal/ path.
internal_case_routes := {
	["GET", "/internal/cases/{case_id}/record"],
	["GET", "/internal/cases/{case_id}/items"],
	["PUT", "/internal/cases/{case_id}/items"],
	["PUT", "/internal/cases/{case_id}/intake"],
	["PATCH", "/internal/cases/{case_id}/state"],
	["POST", "/internal/cases/{case_id}/assign"],
	["POST", "/internal/cases/{case_id}/events"],
	["GET", "/internal/documents/{doc_id}"],
	["PATCH", "/internal/documents/{doc_id}"],
	["GET", "/internal/analysts/load"],
}

test_service_may_call_every_internal_case_route if {
	every r in internal_case_routes {
		authz.allow with input as req("fcrm_service", "case", r[0], r[1])
	}
}

test_no_person_role_may_call_an_internal_case_route if {
	every role in {"fcrm_po", "fcrm_analyst", "fcrm_committee", "fcrm_observer"} {
		every r in internal_case_routes {
			not authz.allow with input as req(role, "case", r[0], r[1])
		}
	}
}

test_the_case_stream_serves_every_person_role_and_the_service if {
	every role in {"fcrm_po", "fcrm_analyst", "fcrm_committee", "fcrm_observer", "fcrm_service"} {
		authz.allow with input as req(role, "case", "GET", "/cases/{case_id}/stream")
	}
}

test_the_stream_is_read_only if {
	every role in {"fcrm_po", "fcrm_analyst", "fcrm_committee", "fcrm_observer"} {
		not authz.allow with input as req(role, "case", "POST", "/cases/{case_id}/stream")
	}
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
	not authz.allow with input as req("fcrm_service", "decision", "POST", "/packs/{pack_id}/release")
	not authz.allow with input as req("fcrm_service", "decision", "POST", "/packs/{pack_id}/threads")
	not authz.allow with input as req("fcrm_service", "decision", "POST", "/threads/{thread_id}/answer")
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

test_audit_proposals_head_and_rows_matrix if {
	# POST /proposals: the service account only (H1 appends what an agent proposed)
	authz.allow with input as req("fcrm_service", "audit", "POST", "/proposals")
	not authz.allow with input as req("fcrm_analyst", "audit", "POST", "/proposals")
	not authz.allow with input as req("fcrm_committee", "audit", "POST", "/proposals")
	not authz.allow with input as req("fcrm_po", "audit", "POST", "/proposals")
	not authz.allow with input as req("fcrm_observer", "audit", "POST", "/proposals")

	# reads: analyst, committee, service; never PO or observer. GET /records is
	# the web's audit/records (row shape), GET /head/{case_id} the chain head.
	every role in ["fcrm_analyst", "fcrm_committee", "fcrm_service"] {
		authz.allow with input as req(role, "audit", "GET", "/proposals")
		authz.allow with input as req(role, "audit", "GET", "/head/{case_id}")
		authz.allow with input as req(role, "audit", "GET", "/records")
	}
	every role in ["fcrm_po", "fcrm_observer"] {
		not authz.allow with input as req(role, "audit", "GET", "/proposals")
		not authz.allow with input as req(role, "audit", "GET", "/head/{case_id}")
		not authz.allow with input as req(role, "audit", "GET", "/records")
	}
}

test_policy_reference_data_matrix if {
	# jurisdictions, controls and field sets: read by analyst, committee and
	# the service account (the agent tools run through H2); never the observer.
	every role in ["fcrm_analyst", "fcrm_committee", "fcrm_service"] {
		every route in [
			"/jurisdictions",
			"/jurisdictions/versions",
			"/controls",
			"/controls/versions",
			"/field-sets/{version}",
			"/field-sets/{version}/resolve",
		] {
			authz.allow with input as req(role, "policy", "GET", route)
		}
	}
	every route in [
		"/jurisdictions",
		"/jurisdictions/versions",
		"/controls",
		"/controls/versions",
		"/field-sets/{version}",
		"/field-sets/{version}/resolve",
	] {
		not authz.allow with input as req("fcrm_observer", "policy", "GET", route)
	}

	# the PO reads only the field labels of a resolved field set
	authz.allow with input as req("fcrm_po", "policy", "GET", "/field-sets/{version}/resolve")
	every route in [
		"/jurisdictions",
		"/jurisdictions/versions",
		"/controls",
		"/controls/versions",
		"/field-sets/{version}",
	] {
		not authz.allow with input as req("fcrm_po", "policy", "GET", route)
	}

	# nobody but the analyst (who owns configuration, D-03) may publish a version
	authz.allow with input as req("fcrm_analyst", "policy", "POST", "/jurisdictions/versions")
	not authz.allow with input as req("fcrm_committee", "policy", "POST", "/jurisdictions/versions")
	not authz.allow with input as req("fcrm_committee", "policy", "POST", "/controls/versions")
	not authz.allow with input as req("fcrm_po", "policy", "POST", "/controls/versions")
}

test_policy_corpus_routes_matrix if {
	# the web's policy search and the precedent search: the analyst and the
	# service account (the agent tools); precedent hits carry a case's band and
	# ratings, so never the committee, the PO or the observer
	every route in ["/policy/search", "/precedents/search"] {
		authz.allow with input as req("fcrm_analyst", "policy", "POST", route)
		authz.allow with input as req("fcrm_service", "policy", "POST", route)
		not authz.allow with input as req("fcrm_committee", "policy", "POST", route)
		not authz.allow with input as req("fcrm_po", "policy", "POST", route)
		not authz.allow with input as req("fcrm_observer", "policy", "POST", route)
	}

	# a clause by id: committee members read the clauses a pack cites
	every role in ["fcrm_analyst", "fcrm_committee", "fcrm_service"] {
		authz.allow with input as req(role, "policy", "GET", "/clauses/{clause_id}")
	}
	every role in ["fcrm_po", "fcrm_observer"] {
		not authz.allow with input as req(role, "policy", "GET", "/clauses/{clause_id}")
	}
}

test_policy_internal_routes_are_the_service_accounts_alone if {
	every r in [
		["GET", "/internal/clauses"],
		["GET", "/internal/precedents"],
		["POST", "/internal/precedents"],
	] {
		authz.allow with input as req("fcrm_service", "policy", r[0], r[1])

		# the analyst wildcard would let a person through; the deny rule stops it
		every role in ["fcrm_analyst", "fcrm_committee", "fcrm_po", "fcrm_observer"] {
			not authz.allow with input as req(role, "policy", r[0], r[1])
		}
	}
}

test_only_the_analyst_activates_a_jurisdiction_list if {
	route := "/jurisdictions/versions/{version}/activate"
	authz.allow with input as req("fcrm_analyst", "policy", "POST", route)

	# Guard 7: the service account inherits the analyst's rules, so an explicit
	# deny is what keeps it from deciding this
	not authz.allow with input as req("fcrm_service", "policy", "POST", route)
	not authz.allow with input as req("fcrm_committee", "policy", "POST", route)
	not authz.allow with input as req("fcrm_po", "policy", "POST", route)
	not authz.allow with input as req("fcrm_observer", "policy", "POST", route)
}

# Every S5 route against every role. The set is what fcrm_decision.routes_*
# serve; a route added there needs a row here (and a rule in permissions.json).
decision_roles := {"fcrm_po", "fcrm_analyst", "fcrm_committee", "fcrm_observer", "fcrm_service"}

# [method, route, the roles allowed]
decision_matrix := [
	# the service account reads every S5 GET (decision-get-all-service); the route's
	# own require_roles narrows the queue to a member
	["GET", "/committee/queue", {"fcrm_committee", "fcrm_service"}],
	["GET", "/cases/{case_id}/packs/latest", {"fcrm_analyst", "fcrm_committee", "fcrm_service"}],
	["GET", "/cases/{case_id}/packs", {"fcrm_analyst", "fcrm_committee", "fcrm_service"}],
	["GET", "/cases/{case_id}/outcome", {"fcrm_analyst", "fcrm_committee", "fcrm_service"}],
	["GET", "/packs/{pack_id}", {"fcrm_analyst", "fcrm_committee", "fcrm_service"}],
	["GET", "/packs/{pack_id}/votes", {"fcrm_analyst", "fcrm_committee", "fcrm_service"}],
	["GET", "/packs/{pack_id}/outcome", {"fcrm_analyst", "fcrm_committee", "fcrm_service"}],
	["GET", "/packs/{pack_id}/threads", {"fcrm_analyst", "fcrm_committee", "fcrm_service"}],
	["POST", "/packs", {"fcrm_analyst", "fcrm_service"}],
	["POST", "/packs/{pack_id}/release", {"fcrm_analyst"}],
	["POST", "/packs/{pack_id}/votes", {"fcrm_committee"}],
	["POST", "/packs/{pack_id}/threads", {"fcrm_committee"}],
	["POST", "/threads/{thread_id}/answer", {"fcrm_analyst"}],
	["POST", "/packs/{pack_id}/supersede", {"fcrm_service"}],
	["POST", "/packs/{pack_id}/revote", {"fcrm_service"}],
	["POST", "/outcomes", {"fcrm_service"}],
]

test_decision_matrix_admits_exactly_the_listed_roles if {
	every row in decision_matrix {
		every role in decision_roles {
			got := authz.allow with input as req(role, "decision", row[0], row[1])
			got == (role in row[2])
		}
	}
}

test_decision_po_has_no_s5_access_at_all if {
	every row in decision_matrix {
		not authz.allow with input as req("fcrm_po", "decision", row[0], row[1])
	}
}

test_decision_observer_has_no_s5_access if {
	# ADR-19: the observer is scoped to the harness monitor.
	every row in decision_matrix {
		not authz.allow with input as req("fcrm_observer", "decision", row[0], row[1])
	}
}

test_decision_only_the_service_supersedes_confirms_revotes_and_records_outcomes if {
	every route in ["/packs/{pack_id}/supersede", "/packs/{pack_id}/revote", "/outcomes"] {
		authz.allow with input as req("fcrm_service", "decision", "POST", route)
		every role in {"fcrm_po", "fcrm_analyst", "fcrm_committee", "fcrm_observer"} {
			not authz.allow with input as req(role, "decision", "POST", route)
		}
	}
}

test_decision_the_service_account_decides_no_human_gate if {
	# Guard 7: it inherits the person roles for reads and may build, but it
	# never releases, votes, asks or answers.
	every route in [
		"/packs/{pack_id}/release",
		"/packs/{pack_id}/votes",
		"/packs/{pack_id}/threads",
		"/threads/{thread_id}/answer",
	] {
		not authz.allow with input as req("fcrm_service", "decision", "POST", route)
	}
}

test_decision_the_removed_routes_are_not_authorised if {
	# Renamed to the web's names (ADR-30): questions/answers are threads/answer,
	# and the stateless release-check route is gone (release answers 409).
	every row in [
		["POST", "/packs/{pack_id}/questions"],
		["POST", "/questions/{question_id}/answers"],
		["POST", "/release-checks"],
		["GET", "/packs/{pack_id}/addenda"],
	] {
		every role in {"fcrm_po", "fcrm_analyst", "fcrm_committee", "fcrm_observer"} {
			not authz.allow with input as req(role, "decision", row[0], row[1])
		}
	}
}

test_decision_wrong_method_is_denied if {
	not authz.allow with input as req("fcrm_committee", "decision", "PUT", "/packs/{pack_id}/votes")
	not authz.allow with input as req("fcrm_committee", "decision", "DELETE", "/packs/{pack_id}/threads")
	not authz.allow with input as req("fcrm_analyst", "decision", "PATCH", "/packs/{pack_id}")
	not authz.allow with input as req("fcrm_service", "decision", "DELETE", "/outcomes")
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
		["audit", "GET", "/records"],
		["audit", "POST", "/proposals"],
		["audit", "GET", "/proposals"],
		["audit", "GET", "/head/{case_id}"],
		["policy", "GET", "/jurisdictions"],
		["policy", "GET", "/controls"],
		["policy", "GET", "/field-sets/{version}"],
		["policy", "POST", "/policy/search"],
		["policy", "POST", "/precedents/search"],
		["policy", "GET", "/clauses/{clause_id}"],
		["policy", "GET", "/internal/clauses"],
		["policy", "POST", "/jurisdictions/versions/{version}/activate"],
		["decision", "GET", "/committee/queue"],
		["decision", "GET", "/cases/{case_id}/packs/latest"],
		["decision", "GET", "/cases/{case_id}/outcome"],
		["decision", "POST", "/packs"],
		["decision", "GET", "/packs/{pack_id}"],
		["decision", "POST", "/packs/{pack_id}/release"],
		["decision", "POST", "/packs/{pack_id}/votes"],
		["decision", "GET", "/packs/{pack_id}/votes"],
		["decision", "GET", "/packs/{pack_id}/outcome"],
		["decision", "POST", "/packs/{pack_id}/threads"],
		["decision", "GET", "/packs/{pack_id}/threads"],
		["decision", "POST", "/threads/{thread_id}/answer"],
		["decision", "POST", "/packs/{pack_id}/supersede"],
		["decision", "POST", "/packs/{pack_id}/revote"],
		["decision", "POST", "/outcomes"],
	] {
		not authz.allow with input as req("fcrm_po", r[0], r[1], r[2])
	}
}

# ── the PO surface (fcrm_case.routes_po): every route it serves ─────────────
# The set is what routes_po.py registers, with the route templates FastAPI
# reports. A route added there without a rule here (or the other way round)
# fails test_every_po_route_has_an_allow_rule_for_the_po.

po_routes := {
	["GET", "/dashboard"],
	["GET", "/cases"],
	["POST", "/cases"],
	["GET", "/cases/{case_id}"],
	["GET", "/cases/{case_id}/documents"],
	["POST", "/cases/{case_id}/documents"],
	["GET", "/cases/{case_id}/intake"],
	["POST", "/cases/{case_id}/intake/categories/{change_type}"],
	["POST", "/cases/{case_id}/intake/values/{field_id}"],
	["POST", "/cases/{case_id}/intake/reuse"],
	["POST", "/cases/{case_id}/intake/answers"],
	["GET", "/cases/{case_id}/review"],
	["POST", "/cases/{case_id}/validate"],
	["POST", "/cases/{case_id}/submit"],
	["POST", "/cases/{case_id}/discard"],
	["GET", "/cases/{case_id}/activity"],
	["GET", "/integrations"],
	["POST", "/integrations/{integration_id}/{action}"],
}

po_write_routes := {r | some r in po_routes; r[0] == "POST"}

# Staff may read a case's documents and its activity feed (their own shapes);
# every other PO read is the owner's alone.
shared_po_reads := {
	["GET", "/cases/{case_id}/documents"],
	["GET", "/cases/{case_id}/activity"],
}

test_every_po_route_has_an_allow_rule_for_the_po if {
	every r in po_routes {
		authz.allow with input as req("fcrm_po", "case", r[0], r[1])
	}
}

test_no_other_person_role_may_call_a_po_write_route if {
	every role in {"fcrm_analyst", "fcrm_committee", "fcrm_observer"} {
		every r in po_write_routes {
			not authz.allow with input as req(role, "case", r[0], r[1])
		}
	}
}

test_the_service_account_never_makes_a_po_decision if {
	# It inherits the PO role for reads, so the deny rules are what stop it.
	every r in po_write_routes {
		not authz.allow with input as req("fcrm_service", "case", r[0], r[1])
	}
}

po_only_reads := {r |
	some r in po_routes
	r[0] == "GET"
	not r in shared_po_reads
}

test_po_reads_are_the_owners_alone_except_documents_and_activity if {
	every role in {"fcrm_analyst", "fcrm_committee", "fcrm_observer"} {
		every r in po_only_reads {
			not authz.allow with input as req(role, "case", r[0], r[1])
		}
	}
}

test_staff_reads_of_documents_and_activity if {
	authz.allow with input as req("fcrm_analyst", "case", "GET", "/cases/{case_id}/documents")
	authz.allow with input as req("fcrm_analyst", "case", "GET", "/cases/{case_id}/activity")
	not authz.allow with input as req("fcrm_committee", "case", "GET", "/cases/{case_id}/activity")
	not authz.allow with input as req("fcrm_observer", "case", "GET", "/cases/{case_id}/documents")
}

test_the_observer_is_read_only_on_the_case_service if {
	# The stream and the monitor, nothing else; never a write.
	authz.allow with input as req("fcrm_observer", "case", "GET", "/cases/{case_id}/stream")
	every r in po_routes {
		not authz.allow with input as req("fcrm_observer", "case", r[0], r[1])
	}
}

test_po_denied_every_analyst_route if {
	every r in {
		["GET", "/queue"],
		["GET", "/cases/search"],
		["GET", "/cases/{case_id}/workbench"],
		["GET", "/cases/{case_id}/submissions"],
		["GET", "/cases/{case_id}/submissions/diff"],
		["GET", "/cases/{case_id}/items"],
		["GET", "/cases/{case_id}/score"],
		["GET", "/cases/{case_id}/recommendation"],
		["POST", "/cases/{case_id}/change-types"],
		["POST", "/cases/{case_id}/screening"],
		["POST", "/cases/{case_id}/completeness"],
		["POST", "/cases/{case_id}/clarifications"],
		["POST", "/cases/{case_id}/acknowledge"],
		["POST", "/cases/{case_id}/controls/planned"],
		["POST", "/cases/{case_id}/gaps"],
		["POST", "/cases/{case_id}/runs"],
		["POST", "/cases/{case_id}/recommendation/lock"],
		["POST", "/cases/{case_id}/recommendation/unlock"],
		["POST", "/cases/{case_id}/close"],
		["POST", "/cases/{case_id}/items/{item_id}"],
		["PUT", "/cases/{case_id}/recommendation"],
		["GET", "/conditions"],
		["PATCH", "/conditions/{cnd_id}"],
		["GET", "/reflags"],
		["POST", "/reflags/{reflag_id}/acknowledge"],
	} {
		not authz.allow with input as req("fcrm_po", "case", r[0], r[1])
	}
}

test_the_old_po_routes_are_gone_from_the_policy if {
	# PUT fields / PUT title / GET versions were the pre-web shapes; nothing
	# authorises them for anyone.
	not authz.allow with input as req("fcrm_po", "case", "PUT", "/cases/{case_id}/fields/{field_id}")
	not authz.allow with input as req("fcrm_po", "case", "PUT", "/cases/{case_id}/title")
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
