# Review checklist

For the G-C2 review of every merge (`/code-review` plus a person), and for any AI reviewer given a diff. Tick each line or write why it does not apply. A single "no" on sections 1–3 blocks the merge.

## 1. The eight rules (rules as code, Guard 5)

- [ ] **R-1 No agent wrote a score.** No agent output model, tool or prompt produces `score`, `composite`, `residual`, `inherent`, `retention`, `band` or a route. Scores come only from `fcrm_scoring`, called by H1 (ADR-07). No agent has a tool backed by S2.
- [ ] **R-2 No bulk accept.** Every gate decision carries exactly one item id. No endpoint, UI action or script accepts several items at once.
- [ ] **R-3 The PO view has no restricted fields.** No PO response model, SSE event or page carries any key on the restricted list (`score, composite, band, route, residual, inherent, retention, ce, control_effectiveness, rating, likelihood, impact, clause, clauses, boundary, critic, precedent_rating, recommendation, vote, pack`). PO-facing text passes `ai/guards/restricted_terms.txt`.
- [ ] **R-4 The analyst never edits the submission.** Analyst actions create clarifications, overrides or notes. No analyst route writes a submission version.
- [ ] **R-5 A closed case is untouched.** Closed-state cases accept only read, examiner and condition-update events.
- [ ] **R-6 An override has a reason.** Every changed value at a gate requires a reason of 20 or more characters, and it is stored on the audit record.
- [ ] **R-7 A planned control is not scored.** `planned=True` means no rating, and the control is absent from the weighted set.
- [ ] **R-8 Policy is quoted, not generated.** Every clause quote is a verbatim substring of the stored clause text at the pinned corpus version. No prompt asks a model to write policy.

## 2. Visibility matrix (`docs/governance/visibility-matrix.md`)

- [ ] Each new or changed route has its OPA permission row (`ops/opa/data/permissions.json`), with default deny.
- [ ] The PO gets 403 on scoring, decision, harness-monitor and analyst routes (ADR-19). The committee sees only cases in its released packs.
- [ ] A new response model for a role other than analyst is defined in that role's module (`fcrm_contracts.api.case_po` and similar), never reused from the analyst model.
- [ ] SSE streams are filtered by role on the server, never in the client.

## 3. Version pins

- [ ] Every scoring call, agent job and pack carries `PinnedVersions` (`scoring_v, corpus_v, controls_v, fieldset_v, jurisdictions_v, routing_v, prompts_v`), taken from the case, never from "latest".
- [ ] A config change creates a new version snapshot and never edits one in place. Open cases keep their pins.
- [ ] Tool results tagged with another version are rejected before a model sees them.
- [ ] A prompt change is a new `v<N+1>` file with a changelog entry, a `MANIFEST.yaml` bump and regenerated locks. No model id appears outside `ai/models/tiers.yaml`.

## 4. Idempotency and failure

- [ ] Every consumer handles redelivery: a duplicate `job_id` or event id is acked without doing the work again.
- [ ] Every POST that a client may retry takes an idempotency key, or is naturally idempotent.
- [ ] Failures are explicit: a job that fails after its one retry opens a `handoff` gate. The DLQ is used, and nothing is silently marked COMPLETED.
- [ ] Timeouts are set on every outbound call (HTTP, Bedrock, OPA, Mongo).

## 5. Audit record for every state change

- [ ] Every state change writes an audit record through S4, in the same Mongo transaction as the outbox event.
- [ ] Human decisions record who, when, the item and the reason.
- [ ] Nothing writes to `fcrm_audit.audit_records` except S4, through the insert-only user. The chain still verifies (`GET /verify/{case}`) after the change.
- [ ] Agent proposals carry `prompt_id@version`, the rendered hash, `pack_hash`, the model id and the attempt.

## 6. Error copy and messages

- [ ] User-facing errors say what happened and what to do next, in plain words. No stack traces, internal ids or rule codes are shown to the PO.
- [ ] API errors use the shared `Problem` shape (status, code, detail) with a stable `code`.
- [ ] Logs use `logging` with context (case id, job id, trace id). They never contain PII, secrets, tokens or full prompts.

## 7. Accessibility (web changes)

- [ ] Every interactive element is reachable and operable by keyboard, with a visible focus state.
- [ ] Every control has a name: label, `aria-label` or text. Icon-only buttons have one.
- [ ] Colour is never the only signal (band chips, status pills, diff marks). Contrast passes WCAG AA, checked with `vitest-axe` on the component.
- [ ] Live regions announce streaming agent progress and gate outcomes without stealing focus.

## 8. Code standards (ADR-21, `ai/guidance/coding-standards.md`)

- [ ] Type hints on every signature, docstrings on public symbols, `Optional[T]`, specific exceptions, `logging` not `print`.
- [ ] Pydantic DTOs use `extra="forbid"`. Shapes are imported from `fcrm_contracts`, never copied.
- [ ] Tests live under `tests/unit/<src path>` or `tests/integration/<src path>`, named `test_<function>_<scenario>`, with fixtures only in `tests/conftest.py`.
- [ ] There is no config inside `src/`, no secret in code, and no business arithmetic in the web client.
- [ ] The commit messages follow `commit-convention.md`.
