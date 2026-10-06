# Route authorization for the oversight lane (examiner, watchlist re-flags and the
# audit analytics), against the real ops/opa/data/permissions.json. The PO is
# denied on every one of them; the service account does what H1 and S1 need and
# never decides a human gate or asks a question.
package authz_oversight_test

import rego.v1

import data.authz

req(role, service, method, route) := {
	"role": role,
	"service": service,
	"method": method,
	"route_template": route,
}

# ── the examiner (S4) ──────────────────────────────────────────────────────

test_examiner_ask_is_for_analyst_and_committee if {
	authz.allow with input as req("fcrm_analyst", "audit", "POST", "/examiner/ask")
	authz.allow with input as req("fcrm_committee", "audit", "POST", "/examiner/ask")
}

test_examiner_ask_is_denied_to_everyone_else if {
	not authz.allow with input as req("fcrm_po", "audit", "POST", "/examiner/ask")
	not authz.allow with input as req("fcrm_observer", "audit", "POST", "/examiner/ask")
	not authz.allow with input as req("fcrm_service", "audit", "POST", "/examiner/ask")
}

test_examiner_answers_are_posted_by_the_service_only if {
	authz.allow with input as req("fcrm_service", "audit", "POST", "/examiner/answers")
	not authz.allow with input as req("fcrm_analyst", "audit", "POST", "/examiner/answers")
	not authz.allow with input as req("fcrm_committee", "audit", "POST", "/examiner/answers")
	not authz.allow with input as req("fcrm_po", "audit", "POST", "/examiner/answers")
	not authz.allow with input as req("fcrm_observer", "audit", "POST", "/examiner/answers")
}

test_examiner_answers_are_read_by_the_askers if {
	route := "/examiner/answers/{question_id}"
	authz.allow with input as req("fcrm_analyst", "audit", "GET", route)
	authz.allow with input as req("fcrm_committee", "audit", "GET", route)
	authz.allow with input as req("fcrm_service", "audit", "GET", route)
	not authz.allow with input as req("fcrm_po", "audit", "GET", route)
	not authz.allow with input as req("fcrm_observer", "audit", "GET", route)
}

test_ask_deny_names_the_service_rule if {
	r := authz.response with input as req("fcrm_service", "audit", "POST", "/examiner/ask")
	r.allow == false
	"deny-audit-post-examiner-ask-service" in r.denied_by
}
