# Route authorization for every TrustOps HTTP service (X2, design A §8.2).
#
# Query:  POST /v1/data/authz/allow     -> {"result": true|false}
#         POST /v1/data/authz/response  -> allow + matched rules + reason (for logs)
#
# Input (either shape is accepted, so fcrm_common.authz and the cloned AEO
# interceptor both work):
#   {"role": "fcrm_analyst", "service": "case", "method": "POST",
#    "route_template": "/cases/{id}/items/{item_id}"}
#   {"service": "case", "method": "POST", "path": "/cases/{id}/items/{item_id}",
#    "user": {"id": "u-riyer", "roles": ["fcrm_analyst"]}}
#
# `route_template` / `path` is the FastAPI route template relative to the
# service's APP_ROOT_PATH, so one decision covers every id and the Redis
# cache key stays small.
#
# Fix carried from the AEO clone: the role hierarchy is data
# (data.permissions.role_hierarchy in ops/opa/data/permissions.json), not a
# map hard-coded in this file, so adding a role never needs a policy change.
package authz

import rego.v1

default allow := false

# ── caller roles ───────────────────────────────────────────────────────────

caller_roles contains r if {
	r := input.role
	is_string(r)
}

caller_roles contains r if {
	some r in input.roles
}

caller_roles contains r if {
	some r in input.user.roles
}

# A role inherits the permissions of every role listed for it. Unknown roles
# inherit nothing, so they fall through to the default deny.
effective_roles contains inherited if {
	some r in caller_roles
	some inherited in data.permissions.role_hierarchy[r]
}

# ── request route and method ───────────────────────────────────────────────

route := input.route_template if {
	is_string(input.route_template)
} else := input.path if {
	is_string(input.path)
}

method := upper(input.method)

# ── decision ───────────────────────────────────────────────────────────────

allow if {
	count(matched_allow) > 0
	count(matched_deny) == 0
}

matched_allow contains rule.id if {
	some rule in data.permissions.rules
	rule.effect == "allow"
	rule_applies(rule)
	some role in rule.roles
	role in effective_roles
}

# Deny rules match the caller's own roles (not inherited ones): they exist to
# stop the fcrm_service account, which inherits every person role for reads,
# from ever deciding a human gate (Guard 7).
matched_deny contains rule.id if {
	some rule in data.permissions.rules
	rule.effect == "deny"
	rule_applies(rule)
	some role in rule.roles
	role in caller_roles
}

rule_applies(rule) if {
	rule.service == input.service
	method_matches(rule.method)
	path_matches(rule.path, rule.service)
}

method_matches(m) if m == "*"

method_matches(m) if upper(m) == method

# ── path matcher (AEO semantics: exact, {param} templates, trailing /*) ────

# Exact.
path_matches(pattern, _) if pattern == route

# Template: same number of segments; a {param} segment matches any segment.
# An exact literal rule for the same service wins over every template, so
# GET /cases/search is never authorised by the PO's GET /cases/{id}.
path_matches(pattern, service) if {
	contains(pattern, "{")
	not endswith(pattern, "/*")
	not exact_route_defined(service)
	segments_match(split(pattern, "/"), split(route, "/"))
}

# Wildcard: "/internal/*" matches one or more further segments; the prefix
# itself may contain {param} segments ("/packs/{pack_id}/*").
path_matches(pattern, _) if {
	endswith(pattern, "/*")
	prefix := split(trim_suffix(pattern, "/*"), "/")
	parts := split(route, "/")
	count(parts) > count(prefix)
	every i, seg in prefix {
		segment_matches(seg, parts[i])
	}
}

segments_match(pattern_parts, route_parts) if {
	count(pattern_parts) == count(route_parts)
	every i, seg in pattern_parts {
		segment_matches(seg, route_parts[i])
	}
}

segment_matches(seg, actual) if seg == actual

segment_matches(seg, _) if is_param(seg)

is_param(seg) if {
	startswith(seg, "{")
	endswith(seg, "}")
}

exact_route_defined(service) if {
	some rule in data.permissions.rules
	rule.service == service
	rule.path == route
	not contains(rule.path, "{")
	not contains(rule.path, "*")
}

# ── enriched response (decision logs, problem+json detail) ─────────────────

response := {
	"allow": allow,
	"user": object.get(object.get(input, "user", {}), "id", null),
	"roles": sort([r | some r in caller_roles]),
	"service": object.get(input, "service", null),
	"method": object.get(input, "method", null),
	"route": route_or_null,
	"matched_rules": sort([id | some id in matched_allow]),
	"denied_by": sort([id | some id in matched_deny]),
	"reason": reason,
}

default route_or_null := null

route_or_null := route

default reason := "no matching allow rule for this role, method and route"

reason := "allowed by policy" if allow

reason := "explicitly denied by policy" if count(matched_deny) > 0

reason := "no role supplied" if count(caller_roles) == 0
