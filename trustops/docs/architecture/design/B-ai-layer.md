# B · AI layer design: agents, context, guards, runtime, memory, evals, tokens

Status: design for implementation by Claude Code subagents without further human input. Written 3 Oct 2026 (build day 1 of 6; submission 9 Oct).
Sources: 00-synthesis, B-build-plan (v1.3), C-blueprint-flow (v2), F-walkthrough-seed, E-aeo-reuse §9, A-problem-statement, CLAUDE.md. Decisions already made by the lead are restated as "decided"; everything this document adds is marked "(proposed)".

Conventions used throughout
- Decided: Pydantic AI 2.x, one agent call per job, run inside H2 agent-worker (FastStream consumer of `agent.run`); H1 Caseflow orchestrates and calls S2 scoring; agents never orchestrate and never score; all models via Bedrock `BedrockConverseModel` behind a `FallbackModel` per tier; embeddings Titan Embed Text v2 (1024-d); nine agents; budgets cheap 6k/60 s, mid 12k/120 s, strong 20k/180 s, one retry on the fallback model, 120k per case; seven guards in fixed order; policy quoted verbatim; monorepo layout; four memory kinds, no agent-memory products.
- Model placeholders: `{{MODEL_CHEAP}}`, `{{MODEL_MID}}`, `{{MODEL_STRONG}}`, `{{MODEL_JUDGE}}`, `{{MODEL_FALLBACK_CHEAP}}`, `{{MODEL_FALLBACK_MID}}`, `{{MODEL_FALLBACK_STRONG}}`, `{{MODEL_FALLBACK_JUDGE}}`. The lead fills the Bedrock IDs in `ai/models/tiers.yaml` only; no other file names a model.
- Pydantic AI symbols used: `Agent`, `RunContext`, `ModelRetry`, `ToolOutput`, `UsageLimits`, `ModelSettings`, `BedrockConverseModel`, `BedrockProvider`, `FallbackModel`, `TestModel`, `FunctionModel`, `pydantic_evals.Dataset/Case/Evaluator`. The implementer verifies each name against the installed version on day 1 (`uv run python -c "import pydantic_ai, inspect; ..."`) and records the version in `ai/models/tiers.yaml: sdk.pydantic_ai`.
- Two refinements of the build plan that this design depends on (proposed; add to the decision log as D-20a and D-20b):
  - D-20a: the assessment agent has **no** `scoring_engine` tool. H1 calls `POST /score` after the assessment proposals land (sequence step 15/28). The seed's agent row listing `scoring_engine` is corrected in gen_seed.
  - D-20b: H1 defines the working memory (a small `JobPayload` with references and pinned versions); H2 **materialises** it into a `ContextPack` through the per-agent builder, snapshots the pack (hash + MinIO key) and writes the hash to the evidence ledger. RabbitMQ messages stay small, and any call can be rebuilt byte-for-byte from `pack_hash`.

---

## 1. `/ai` folder: tree, file formats, locks

### 1.1 Tree (proposed)

```
/ai
  README.md                         what each sub-folder is, how it is loaded, how to change a prompt
  CLAUDE.md                         rules for Claude Code subagents working under /ai (see 1.9)
  agents/                           one spec per agent (nine files)
    screening.agent.yaml  intake.agent.yaml  research.agent.yaml  assessment.agent.yaml
    critic.agent.yaml  narrative.agent.yaml  examiner.agent.yaml  watchlist.agent.yaml  policy_gap.agent.yaml
  tools/                            one spec per tool (thirteen files)
    document_reader.tool.yaml  case_record.tool.yaml  case_search.tool.yaml  field_rules.tool.yaml
    policy_search.tool.yaml  precedent_search.tool.yaml  control_inventory.tool.yaml  evidence_store.tool.yaml
    jurisdiction_list.tool.yaml  rules_check.tool.yaml  evidence_ledger.tool.yaml  audit_log.tool.yaml
    submission_diff.tool.yaml       (new, proposed: v1→v2 changed fields for re-runs)
  skills/                           instruction packs included into system prompts
    cite_verbatim.skill.md  declare_gap.skill.md  compare_precedent.skill.md
    draft_for_business_reader.skill.md  batch_questions.skill.md
  prompts/
    MANIFEST.yaml                   prompt set version (= the case pin `prompts_v`) and the CURRENT version of every prompt id
    <agent>/<prompt_id>.v<N>.md     e.g. research/research.system.v1.md, research/research.user.v1.md
    narrative/narrative.completeness.user.v1.md, narrative/narrative.recommendation.user.v1.md
  models/tiers.yaml                 tiers, model placeholders, budgets, settings, price table, SDK versions
  guards/
    guards.yaml                     the seven guards: order, stage, failure behaviour, which agents they apply to
    thresholds.yaml                 numeric thresholds (quote min length, confidence floor, stale months, ...)
    injection_patterns.txt          Guard 1 pattern list (one regex per line, `#` comments)
    pii_entities.yaml               presidio entity list and the signature-block regex
    restricted_terms.txt            terms that may never appear in a PO-facing text
  policies/
    tools.rego  tool_permits.json   OPA agent→tool permits (copied into /ops/opa at build; this is the source)
  registry.lock/
    agents.lock  tools.lock  skills.lock  prompts.lock   (sorted `name<TAB>version<TAB>sha256` lines)
  schemas/                          JSON Schema exports of every output and tool schema, generated by `make schemas`;
                                    read by the panel and by the web app's typegen, never by the runtime
```

Runtime loading: the `fcrm_prompts_loader` package (P4) reads `/ai` at process start, validates every YAML against the contracts models (`AgentSpecFile`, `ToolSpecFile`, `TierFile`, `GuardConfig`), builds the four registries, and compares them with `registry.lock/*`. A mismatch is a startup failure in H2 and a test failure in CI (`make check`). `/ai` is mounted read-only into the H2 image at `/app/ai`.

### 1.2 Agent spec YAML

```yaml
# ai/agents/research.agent.yaml
name: research
purpose: find applicable policy clauses and the closest closed cases; quote verbatim; declare gaps
lifecycle_state: assess                # H1 CaseState that dispatches it
tier: strong
budget: {tokens: 20000, wall_s: 180, retries: 1}    # must equal the tier's budget (lock test)
tools: [policy_search, precedent_search, jurisdiction_list]
tool_call_cap: 3                        # extra model→tool round-trips allowed beyond the pre-retrieved pack
skills: [cite_verbatim, declare_gap, compare_precedent]
context_pack: research                  # builder class name in fcrm_agents.context
output_schema: fcrm_contracts.outputs.ResearchOutput
prompt: {system: research.system, user: research.user}   # versions come from prompts/MANIFEST.yaml
guards: [input_scan, permit, budget, output_shape, rules] # 6 critic and 7 human gate are scheduled by H1
gate: {kind: analyst_per_item, items: [ClauseProposal, PolicyGap], rule: "accept or remove each clause; a removal needs a reason"}
eval: {datasets: [research/clauses-40, research/gaps-12], primary_metric: clause_recall_at_k, gated_metrics: [citation_validity, clause_recall_at_k, gap_honesty]}
reruns_on: [changed_fields]             # clarification round: H1 re-dispatches with payload.changed_fields
owner: fcrm_analyst
```

Fields are the same for all nine agents; `narrative` adds `modes: [completeness, recommendation]` with a per-mode `output_schema` and user prompt id.

### 1.3 Tool spec YAML

```yaml
# ai/tools/policy_search.tool.yaml
name: policy_search
backing: {service: policy-service, method: GET, path: /policy/search}
read_only: true                          # enforced: the client class has no write methods and sends only GET
permit_key: tools.policy_search          # OPA input.tool
timeout_s: 8
cost_class: io                           # io | compute | none (used in the token/latency report)
input_schema: fcrm_contracts.tools.PolicySearchIn    # query: str(≤300), filters{corpus_v, domains[], change_types[], jurisdiction?}, top_k: int ≤ 12
output_schema: fcrm_contracts.tools.PolicySearchOut  # clauses[{clause_id, source_id, source_title, section, text, corpus_v, score}]
cache: {key: [query, filters, top_k], scope: corpus_v, ttl_s: 86400}   # retrieval cache (3.5)
scope_rule: "filters.corpus_v must equal payload.versions.corpus_v"    # pinned-version check done in the client
```

Backing services (proposed where the build plan left them blank): `control_inventory` and `evidence_store` → policy-service (`GET /controls?controls_v=&domains=`; `GET /controls/{id}/evidence?controls_v=` returns artefact, last_tested, scope) plus case-service (`GET /cases/{id}/documents?kind=evidence`) for case-attached evidence; `rules_check` → in-process call into `fcrm_guardrails.rules` (no network); `submission_diff` → case-service (`GET /cases/{id}/submissions/diff?from=&to=`).

### 1.4 Skill files

`ai/skills/<name>.skill.md`: YAML front-matter `{id, version, applies_to: [agents], changelog}` then plain Markdown instructions, 8–20 lines, imperative voice, no variables. Included into system prompts with `{{ skill("cite_verbatim") }}`. The five skills:
- `cite_verbatim`: every claim carries `source_ref` + `quote`; the quote is copied character for character (ellipses only at the ends); never merge two passages into one quote; if a sentence is split across lines, keep the line range honest.
- `declare_gap`: when nothing supports a claim, output a gap item naming what was searched (queries, filters, versions); never fill a gap with a plausible sentence; a gap is a result, not a failure.
- `compare_precedent`: compare like with like (same control, same change types); report the deviation as a number against the precedent set given; never adjust a rating to match a precedent, only flag.
- `draft_for_business_reader`: plain words, no scores, bands, ratings, clause ids or control ids; say what was decided, why in one sentence, and what the owner must do by when.
- `batch_questions`: one round; each question anchored to a field or control id; drop a question that the record already answers and say why; at most five.

### 1.5 Prompt files (Markdown + front-matter, Jinja)

```markdown
---
id: research.system
agent: research
role: system
version: 1
model_tier: strong
cache_prefix: true                     # this whole file is part of the stable, cacheable prefix
variables: []                          # system prompts take no case variables; config excerpts come via includes
includes: [skill:cite_verbatim, skill:declare_gap, skill:compare_precedent, schema:ResearchOutput]
output_schema: fcrm_contracts.outputs.ResearchOutput
changelog:
  - {version: 1, date: 2026-10-04, reason: initial, eval_run: null}
---
(prompt body)
```

User prompts declare `variables: [{name: case, type: CaseFields}, {name: clauses, type: list[RetrievedClause]}, ...]` and `cache_prefix: false`. Rendering is Jinja2 with `StrictUndefined`, autoescape off, a sandboxed environment, exactly four custom functions and two read-only namespaces:
- `skill(id)`: includes a skill file.
- `schema(name)`: renders the JSON Schema of an output model, compact, with field descriptions.
- `section(name, items, cap)`: renders a pack section under its token cap (§3.3); user prompts only.
- `config(key)`: renders a **version-pinned config excerpt** (`routing.R4`, `scoring.scales`, `corpus.catalogue`, `controls.brief`) resolved from the job's `PinnedVersions`. Allowed in system prompts because the text is identical for every case that pins the same versions, so the cached prefix stays stable; the cache key is (prompt version, pinned versions).
- Namespaces: `thresholds` (from `guards/thresholds.yaml`) and `versions` (the pins). No case variable may appear in a system prompt (`test_system_prompts_have_no_case_variables`).
`fcrm_prompts_loader.render(prompt_id, variables) -> RenderedPrompt(text, prompt_id, version, sha256)`; the sha256 of the **template file** is what the lock holds, the sha256 of the **rendered text** goes into the model-call record.

Versioning rule: a prompt file is immutable once committed. A change is a new file `vN+1` plus a changelog entry whose `eval_run` points to a results file (§6.4); `MANIFEST.yaml` moves `current:` to the new version and bumps `set_version` (ISO date, plus `.n` suffix for a second bump on one day). Cases pin `prompts_v = set_version`; the ledger stores `prompt_id@version` and the rendered hash on every proposal.

### 1.6 Model tiers file

```yaml
# ai/models/tiers.yaml
provider: bedrock
region: ${AWS_REGION}
sdk: {pydantic_ai: "<filled on day 1>", boto3: "<filled>"}
embeddings: {model: amazon.titan-embed-text-v2:0, dims: 1024, normalize: true, used_by: [H4 context-worker, policy-service]}
tiers:
  cheap:  {primary: "{{MODEL_CHEAP}}",  fallback: "{{MODEL_FALLBACK_CHEAP}}",  budget: {tokens: 6000,  wall_s: 60,  retries: 1}, settings: {temperature: 0, max_tokens: 2000}, cache_min_prefix_tokens: 4096}
  mid:    {primary: "{{MODEL_MID}}",    fallback: "{{MODEL_FALLBACK_MID}}",    budget: {tokens: 12000, wall_s: 120, retries: 1}, settings: {temperature: 0, max_tokens: 3500}, cache_min_prefix_tokens: 1024}
  strong: {primary: "{{MODEL_STRONG}}", fallback: "{{MODEL_FALLBACK_STRONG}}", budget: {tokens: 20000, wall_s: 180, retries: 1}, settings: {temperature: 0, max_tokens: 5000}, cache_min_prefix_tokens: 1024}
  judge:  {primary: "{{MODEL_JUDGE}}",  fallback: "{{MODEL_FALLBACK_JUDGE}}",  budget: {tokens: 20000, wall_s: 180, retries: 1}, settings: {temperature: 0, max_tokens: 4000}, cache_min_prefix_tokens: 1024}
per_case_budget_tokens: 120000
prices_usd_per_mtok:                   # Bedrock list prices, filled by the lead; used only for the cost column
  "{{MODEL_CHEAP}}": {input: 0, output: 0, cache_read: 0, cache_write: 0}
  # ... one row per placeholder
```

`cache_min_prefix_tokens` is the Bedrock/Anthropic minimum cacheable prefix for the model family (1024 for Sonnet-class, 4096 for Haiku-class); the pack builder uses it to decide whether to emit a cache point at all (§3.4). "strong + judge" for the critic means tier `judge` (budget identical to strong).

### 1.7 Guard config

```yaml
# ai/guards/guards.yaml
order: [input_scan, permit, budget, output_shape, rules, critic, human_gate]
guards:
  input_scan:  {stage: pre,   runs_in: [H3, H2], on_fail: block_and_flag, writes: [ledger, audit:guard.input]}
  permit:      {stage: pre,   runs_in: [H2],     on_fail: block,          writes: [audit:guard.permit]}
  budget:      {stage: pre,   runs_in: [H1, H2], on_fail: fallback_then_human, writes: [audit:guard.budget]}
  output_shape:{stage: post,  runs_in: [H2],     on_fail: retry_on_fallback_then_human, writes: [ledger, audit:guard.output]}
  rules:       {stage: state, runs_in: [H1, H2], on_fail: refuse_state_change, writes: [audit:guard.rules]}
  critic:      {stage: state, runs_in: [H1],     on_fail: n/a (findings shown), writes: [ledger, audit:critic.findings]}
  human_gate:  {stage: state, runs_in: [H1],     on_fail: case_waits,     writes: [audit:gate.decided]}
```
`thresholds.yaml` (proposed values): `quote_min_chars: 12`, `quote_line_tolerance: 2`, `confidence_floor: 0.5` (items below are kept but shown as "low confidence", never dropped by code), `stale_evidence_months: 24`, `precedent_deviation_flag: 1.5`, `injection_score_block: 0.8`, `max_questions_per_round: 5`, `po_summary_max_words: 180`, `pack_section_caps` (see §3.3).

### 1.8 Registry locks and lock tests

Lock files are regenerated only by `make locks` and committed. `tests/registry/test_locks.py` (runs in `make check` and pre-commit):
- `test_agents_lock`: the agent registry built from `/ai/agents` equals `agents.lock` (names, versions, sha256 of each YAML); a diff is printed.
- `test_tools_lock`, `test_skills_lock`: same shape.
- `test_prompts_lock`: every `prompts/**/*.md` sha256 equals the lock; a changed file with the same version fails with "prompt changed without a version bump".
- `test_manifest_points_to_existing_versions`; `test_every_agent_tool_is_registered`; `test_every_agent_tool_has_permit` (agents × tools ⊆ `tool_permits.json`); `test_budget_matches_tier`; `test_no_model_ids_outside_tiers` (grep for `anthropic.`/`amazon.`/`us.` model-id shapes in `/ai` except `models/tiers.yaml`); `test_output_schema_importable_and_extra_forbid`; `test_every_prompt_has_eval_run_after_v1`.

### 1.9 `/ai/CLAUDE.md` (guidance for Claude Code subagents)

Rules, each one line: never edit a committed prompt, add `vN+1` and a changelog entry; run `make evals AGENT=<name>` before moving `MANIFEST.current`; never write a model id anywhere but `models/tiers.yaml`; adding a tool = tool YAML + permit row + lock regen + one contract test; an agent may not gain a write tool; output schemas live in `src/packages/contracts`, prompts only `{{ schema(...) }}` them; every claim-bearing field needs `Provenance`; keep system prompts free of case variables (they are cached); do not raise a budget without a decision-log row; `make locks` after any change under `/ai`.

---

## 2. The nine agents

Shared contracts (`src/packages/contracts/fcrm_contracts/provenance.py`):

```python
class DocRef(BaseModel):     kind: Literal["doc"] = "doc"; doc_id: str; page: int = Field(ge=1); line_start: int = Field(ge=1); line_end: int = Field(ge=1)
class ClauseRef(BaseModel):  kind: Literal["clause"] = "clause"; clause_id: str; corpus_v: str
class RecordRef(BaseModel):  kind: Literal["record"] = "record"; case_id: str; seq: int = Field(ge=1)
class CaseRef(BaseModel):    kind: Literal["case"] = "case"; case_id: str; field_id: str | None = None
class ControlRef(BaseModel): kind: Literal["control"] = "control"; control_id: str; controls_v: str
class OwnerRef(BaseModel):   kind: Literal["owner_answer"] = "owner_answer"; case_id: str; submission_v: int; question_id: str
class ListRef(BaseModel):    kind: Literal["jurisdiction_list"] = "jurisdiction_list"; list_v: str; country: str
SourceRef = Annotated[DocRef | ClauseRef | RecordRef | CaseRef | ControlRef | OwnerRef | ListRef, Field(discriminator="kind")]

class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: SourceRef
    quote: str = Field(min_length=12, max_length=600, description="verbatim text copied from the source span")
    confidence: float = Field(ge=0.0, le=1.0)

class Searched(BaseModel):   # what a gap/refusal was based on
    queries: list[str] = Field(min_length=1, max_length=6); filters: dict[str, str | list[str]] = {}; versions: dict[str, str]

class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a stray "score"/"band"/"composite" key is a validation error (rule 1)
    notes_for_analyst: list[str] = Field(default_factory=list, max_length=5)
```

Every output model below extends `AgentOutput`. Every item that asserts a fact about the case or the corpus carries `provenance: Provenance` (or `provenance: list[Provenance]` with `min_length=1`). Items that assert the *absence* of something carry `searched: Searched` instead. Guard 4 resolves every `SourceRef` and checks every `quote` (§4.4).

Lifecycle (H1 CaseState, decided): `intake → review_and_submit → triage → screening → assess → critic → completeness → clarification → review → decide → oversight`. Agents by state: intake (intake, re-run after owner answers), screening (screening), assess (research ∥ assessment), critic (critic), completeness (narrative.completeness), clarification (research ∥ assessment re-run on changed fields), review → recommendation (narrative.recommendation), oversight (examiner on demand, watchlist on list publication, policy_gap weekly or on gap recorded).

### 2.1 screening (cheap)

Purpose and place: after triage, propose or decline the fast lane (R4). The numeric and categorical criteria are evaluated by code; the agent classifies the one thing code cannot read from structured fields: whether the free-text description is a renewal of an existing vendor arrangement or a feature with no new geography, segment or product. H1 computes `fast_lane = code_criteria_met and agent.classification.qualifies`; the analyst confirms.

Inputs (pack): case fields F01–F06 plus any filled type fields, change types confirmed, the R4 rule text (routing v4), the three nearest precedents' titles and fast-lane flags, `volume_usd` parsed by code.
Tools: `field_rules` (which fields exist for the types), `precedent_search` (top 3, titles only). Tool-call cap 1.

```python
class ScreeningClassification(BaseModel):
    is_vendor_renewal: bool; is_feature_only: bool; introduces_new_geography: bool; introduces_new_segment: bool; introduces_new_product: bool
    provenance: list[Provenance] = Field(min_length=1)
class ScreeningOutput(AgentOutput):
    classification: ScreeningClassification
    qualifies_for_fast_lane: bool          # the agent's reading of R4's text conditions only; code adds the volume test
    reasons: list[str] = Field(min_length=1, max_length=4)
    rule_ref: Literal["R4"] = "R4"
```

System prompt skeleton (`screening.system.v1`):
```
You are the screening step of a bank's financial-crime change-risk workbench. You read one change
request and decide whether its description matches the fast-lane conditions of routing rule R4.
You do not assess risk, you do not score, and you do not decide: an FCRM analyst confirms or rejects.
Rule R4 (quoted, routing {{ versions.routing_v }}): {{ config("routing.R4") }}
What you may conclude from: the case fields shown, the confirmed change types, and the precedent titles.
Classify five facts: vendor renewal; feature-only change; new geography; new customer segment; new product.
For each fact give at least one provenance item: the field id or document span and the verbatim text.
The annual-volume test is done by code from F05; do not reason about the dollar threshold.
A change whose types include new_geography, new_segment or new_product cannot qualify; say so in reasons.
If the description is too thin to classify, set qualifies_for_fast_lane=false and say what is missing.
{{ skill("declare_gap") }}
Output exactly one object matching this schema and nothing else: {{ schema("ScreeningOutput") }}
```
User template (`screening.user.v1`):
```
Case {{ case.id }} · submission v{{ case.submission_v }} · change types: {{ case.change_types | join(", ") }}
Fields (id · label · value · source):
{{ section("fields", case.fields, cap=600) }}
Code-derived facts: annual_volume_usd={{ derived.volume_usd }} · existing_vendor_match={{ derived.vendor_known }}
Nearest closed cases (title · types · fast_lane): {{ section("precedents", precedents, cap=300) }}
Classify and answer in the schema.
```
Gate: analyst confirms or rejects the fast lane (R4). Eval: `fast_lane_precision`, `fast_lane_recall` on `screening/fast-lane-100`. Tokens: pack ≈ 1.4k in, ≈ 300 out; ≈ 1.8k per run.

### 2.2 intake (cheap; one job per document window)

Purpose and place: state `intake`. Reads canonical Markdown of one uploaded document and proposes field values with page:line sources, change types with sources, and gap questions for required fields still empty. Re-runs after the owner answers (state `clarification`) only on the new document and the changed fields. Because the cheap budget is 6k tokens per run, H1 shards intake: one job per document, and a document whose ranked pages exceed the pack cap gets a continuation job (`payload.page_window = [start, end]`). Code merges the shards: dedupe by `field_id`, keep the highest-confidence candidate, keep the rest as `candidates` (this is what the walkthrough shows as "2 candidates" on F05).

Inputs (pack): field set v6 for the confirmed or candidate change types (id, label, type, synonyms); the document's pages selected by the deterministic page ranker (BM25 over page text with field synonyms; top pages until the section cap); prior extractions from other shards (ids and values only, so the agent does not repeat them); the jurisdiction list version (country names only, for F20).
Tools: `document_reader` (read a page range of the same document; cap 2 calls), `field_rules`, `jurisdiction_list`, `precedent_search` (used by the *reuse* step, which code runs after types are confirmed; the agent itself only proposes). Tool-call cap 2.

```python
class Candidate(BaseModel):            value: str; provenance: Provenance
class ExtractionProposal(BaseModel):   field_id: str; value: str; value_type: Literal["text","date","money","int","enum","country_pair"]; provenance: Provenance; candidates: list[Candidate] = Field(default_factory=list, max_length=3)
class CategoryProposal(BaseModel):     change_type: Literal["new_product","new_feature","process_change","vendor_onboarding","new_geography","new_segment"]; provenance: Provenance
class GapQuestion(BaseModel):          field_id: str; question: str = Field(max_length=240); blocking: bool; why: str = Field(max_length=160)
class FactFlag(BaseModel):             statement: str = Field(max_length=240); provenance: Provenance; conflicts_with: SourceRef | None = None
class IntakeOutput(AgentOutput):
    extractions: list[ExtractionProposal] = Field(max_length=25)
    categories: list[CategoryProposal] = Field(max_length=6)
    gaps: list[GapQuestion] = Field(max_length=8)
    facts_flagged: list[FactFlag] = Field(max_length=5)
    not_found: list[str] = Field(default_factory=list, description="field ids searched for in this window and not present")
```

System prompt (`intake.system.v1`):
```
You are the intake reader of a bank's change-risk workbench. You read pages of one business document
and extract values for a fixed list of fields. A product owner will confirm or correct every value.
Rules you must keep:
1. Every value you output points to the page and line range where it appears and quotes it verbatim.
   If you cannot point to a span, do not output the value. Put the field id in not_found instead.
2. Do not infer, normalise or compute values. Copy "Q1 2027" as written; code handles formats.
3. When a field appears more than once with different values, output the most specific as value and
   the others as candidates, each with its own span. Never average or merge.
4. Propose a change type only when a passage supports it; quote the passage.
5. A fact that contradicts another passage or an earlier extraction is a flag, not a correction.
6. Gap questions are for required fields with no value anywhere in this window; one short question per
   field, marked blocking if the field is always-required (F01–F06).
7. You never see or write risk scores, ratings or bands. Do not comment on risk.
{{ skill("cite_verbatim") }}
{{ skill("declare_gap") }}
Lines are numbered inside each page as "p.N l.M: text". Use those numbers.
Output one object matching: {{ schema("IntakeOutput") }}
```
User template (`intake.user.v1`):
```
Case {{ case.id }} · document {{ doc.id }} "{{ doc.name }}" · pages {{ window.start }}–{{ window.end }} of {{ doc.pages }}
Fields to look for (id · label · type · synonyms):
{{ section("field_set", fields, cap=700) }}
Already extracted in other windows (do not repeat): {{ prior_ids | join(", ") }}
Document text with page and line anchors:
{{ section("document", pages, cap=3400) }}
Return the schema object.
```
Gate: PO confirms each category (removal needs a reason) and each value on Review & submit (D-17); the analyst later sees owner corrections with the original kept. Evals: `field_extraction_accuracy` (normalised exact match), `source_span_accuracy` (page match and line overlap), `category_f1`, `gap_question_precision`. Tokens: ≈ 4.5k in, ≈ 900 out per window; the demo's two documents (23 pages) run as 4–5 windows, ≈ 22–26k for intake in total. This is the single biggest token consumer per case and is reported as such (§8).

### 2.3 research (strong)

Purpose and place: state `assess`, parallel with assessment. From the submitted fields, retrieve and propose applicable policy clauses (quoted verbatim from corpus v14), the five closest closed cases, jurisdiction notes, and explicit gaps. Re-runs on changed fields in `clarification`.

Inputs (pack): case fields; change types and domains in scope; pre-retrieved clauses (one deterministic query per in-scope domain and per change type built from a query template over the field values; top 12 after rank fusion, with scores); pre-retrieved precedents (top 5 with similarity, outcome, closed date, control ratings for the in-scope domains); jurisdiction list status for every country in F20; the policy corpus catalogue (source id, title, kind, clause count) so the agent knows what exists.
Tools: `policy_search` (cap 3 extra queries for topics the pre-retrieval missed, e.g. "cash pickup agent"), `precedent_search` (cap 1), `jurisdiction_list` (cap 1).

```python
class ClauseProposal(BaseModel):
    clause_id: str; source_id: str; domain: Literal["customer","product","geography","channel"]
    quote: str = Field(min_length=20, max_length=600)          # verbatim from the clause text (Guard 4 checks)
    why_applicable: str = Field(max_length=280)
    implies_controls: list[str] = Field(default_factory=list, max_length=4)   # control ids from the inventory shown
    provenance: Provenance                                     # source_ref is the ClauseRef; quote duplicated here on purpose
class PrecedentMatch(BaseModel):
    case_id: str; similarity: float = Field(ge=0, le=1)       # copied from the tool result; Guard 4 checks equality
    why_similar: str = Field(max_length=200); ratings_of_interest: list[dict[str, float]] = Field(default_factory=list)
class PolicyGap(BaseModel):
    topic: str = Field(max_length=120); statement: str = Field(max_length=240); searched: Searched; domain: str
class JurisdictionNote(BaseModel):
    country: str; status: Literal["call_for_action","increased_monitoring","not_listed"]; provenance: Provenance   # ListRef + verbatim list line
class ResearchOutput(AgentOutput):
    clauses: list[ClauseProposal] = Field(max_length=8)
    precedents: list[PrecedentMatch] = Field(max_length=5)
    gaps: list[PolicyGap] = Field(max_length=4)
    jurisdiction_notes: list[JurisdictionNote] = Field(max_length=6)
```

System prompt (`research.system.v1`):
```
You are the research step of a bank's AML/CFT and sanctions change-risk workbench. You select the
policy clauses that apply to one proposed change and the closed cases most like it. An FCRM analyst
accepts or removes each clause; nothing you write is final.
Hard rules:
1. Policy is quoted, never written. A clause proposal copies the clause text verbatim from the corpus
   version shown; you may shorten with leading or trailing ellipses only. Never paraphrase, never
   combine two clauses, never cite a clause that is not in the retrieved set or your own search results.
2. Prefer the most specific clause (internal standard over public manual) and say in one sentence why
   it applies to this change. List the control ids it implies, from the inventory shown.
3. If a topic in the case has no clause in the corpus, declare a gap: name the topic, what you searched
   and with which filters and versions. "Nothing found" is a correct answer; an invented clause is not.
4. Precedents: copy the similarity number exactly as given; explain the match in one sentence.
5. Jurisdictions: report the list status of each destination country from the list version shown and
   quote the list line. Do not rate country risk; that is a scoring input the analyst sets.
6. You do not output likelihood, impact, ratings, scores or bands. Do not comment on routing.
Corpus available ({{ versions.corpus_v }}): {{ config("corpus.catalogue") }}   (source id · title · kind · clauses)
{{ skill("cite_verbatim") }} {{ skill("declare_gap") }} {{ skill("compare_precedent") }}
Output one object matching: {{ schema("ResearchOutput") }}
```
User template (`research.user.v1`):
```
Case {{ case.id }} · submission v{{ case.submission_v }} · types {{ case.change_types | join(", ") }} · domains in scope {{ case.domains | join(", ") }}
Versions in force: corpus {{ versions.corpus_v }} · controls {{ versions.controls_v }} · jurisdictions {{ versions.jurisdiction_v }}
{% if changed_fields %}Re-run: only these fields changed since the last run: {{ changed_fields | join(", ") }}. Re-examine clauses touching them; keep others unchanged by id.{% endif %}
Case fields: {{ section("fields", case.fields, cap=700) }}
Retrieved clauses (id · source · section · score · text): {{ section("clauses", clauses, cap=2400) }}
Closest closed cases (id · title · types · outcome · similarity · ratings): {{ section("precedents", precedents, cap=900) }}
Jurisdiction list lines for {{ countries | join(", ") }}: {{ section("jurisdictions", list_lines, cap=300) }}
Controls in scope (id · name · domain): {{ section("controls", controls_brief, cap=500) }}
If a topic in the fields has no retrieved clause, you may call policy_search up to {{ tool_call_cap }} times before declaring a gap.
```
Gate: analyst accepts or removes each clause one by one (no accept-all); a declared gap is recorded by the analyst ("Note gap") and feeds policy_gap. Evals: `citation_validity` (quote ⊂ clause text, measured first-attempt), `clause_recall_at_k` against labelled clause sets, `clause_precision`, `gap_honesty` (no clause proposed for topics the corpus lacks). Tokens: ≈ 6.5k in, ≈ 1.6k out ≈ 8k; re-run on changed fields ≈ 5k.

### 2.4 assessment (strong)

Purpose and place: state `assess`, parallel with research. Proposes likelihood and impact per in-scope domain with a written justification, and for each inventory control in those domains: applicable or not, a strength rating 0–10 with the evidence artefact and date, or `planned` with no rating. It never computes a score; H1 sends the confirmed inputs to S2 (D-20a).

Inputs (pack): case fields; the scoring model's L and I scale definitions (verbatim from scoring v3, so the agent rates against written definitions); control inventory v9 rows for the in-scope domains (id, name, weight, evidence artefact, last tested); evidence available for this case (control evidence from the inventory plus case documents that mention the control, with spans); precedent ratings for the same controls (from the 5 closest cases); the research proposals if already landed (optional: H1 passes `research_proposal_id` when research finished first; otherwise the pack carries the pre-retrieved clauses).
Tools: `control_inventory`, `evidence_store`, `precedent_search` (cap 1). Tool-call cap 2.

```python
class FactorProposal(BaseModel):
    domain: Literal["customer","product","geography","channel"]
    likelihood: int = Field(ge=1, le=5); impact: int = Field(ge=1, le=5)
    justification: str = Field(max_length=400); provenance: list[Provenance] = Field(min_length=1, max_length=4)
    precedent_refs: list[str] = Field(default_factory=list, max_length=5)
class EvidenceNote(BaseModel):
    artefact: str = Field(max_length=120); date: date | None; scope: str = Field(max_length=160); provenance: Provenance
class ControlProposal(BaseModel):
    control_id: str; domain: Literal["customer","product","geography","channel"]
    applicable: bool
    planned: bool = False
    rating: float | None = Field(default=None, ge=0, le=10, multiple_of=0.1)
    evidence: EvidenceNote | None = None
    rationale: str = Field(max_length=240)
    @model_validator(mode="after")
    def planned_has_no_rating(self):   # rule 7 at the schema level
        if self.planned and self.rating is not None: raise ValueError("a planned control carries no rating")
        if self.applicable and not self.planned and (self.rating is None or self.evidence is None): raise ValueError("an applicable control needs a rating and evidence")
        return self
class AssessmentOutput(AgentOutput):
    factors: list[FactorProposal] = Field(min_length=1, max_length=4)
    controls: list[ControlProposal] = Field(min_length=1, max_length=24)
```

System prompt (`assessment.system.v1`):
```
You are the assessment step of a bank's AML/CFT and sanctions change-risk workbench. For one proposed
change you propose inputs to a deterministic scoring engine: likelihood and impact per risk domain in
scope, and a strength rating for each existing control, with its evidence. An FCRM analyst confirms or
overrides every item. You never compute a score, residual, composite or band; code does that.
Scales (scoring model {{ versions.scoring_v }}, quoted): {{ config("scoring.scales") }}
Rules:
1. Rate against the written scale. Justify with at least one quoted passage from the case documents
   or an owner answer; refer to precedent cases by id when they informed you.
2. For each control in the inventory for the domains in scope decide: applicable or not. An applicable
   control gets a rating 0–10 and the evidence artefact, its date and scope, quoted from where you saw it.
3. A control that is scheduled, planned or "to be completed" is planned=true with no rating. Planned
   controls are never scored. Say so in the rationale.
4. Evidence older than {{ thresholds.stale_evidence_months }} months, or whose scope does not cover this
   change (other geography, domestic only, other product), must be named in scope and rated accordingly.
   The staleness flag itself is set by code from the date you report.
5. Where the five precedent cases rated the same control, your rationale states their range. Do not
   copy a precedent rating; rate the evidence in front of you.
6. No evidence means no rating: set applicable=true, rating=null is not allowed; instead rate low and
   state "no evidence seen" in the rationale, quoting the control description as provenance.
{{ skill("cite_verbatim") }} {{ skill("compare_precedent") }}
Output one object matching: {{ schema("AssessmentOutput") }}
```
User template (`assessment.user.v1`):
```
Case {{ case.id }} · v{{ case.submission_v }} · domains in scope {{ case.domains | join(", ") }} · controls {{ versions.controls_v }}
{% if changed_fields %}Re-run on changed fields {{ changed_fields | join(", ") }} and new document(s) {{ new_docs | join(", ") }}. Re-rate only controls touched by them; repeat the others unchanged.{% endif %}
Case fields: {{ section("fields", case.fields, cap=700) }}
Controls in scope (id · name · weight · evidence artefact · last tested): {{ section("controls", controls, cap=1300) }}
Evidence seen for this case (control · artefact · date · scope · span text): {{ section("evidence", evidence, cap=1200) }}
Precedent ratings (case · control · rating · closed): {{ section("precedent_ratings", precedent_ratings, cap=700) }}
Clauses already proposed by research (id · implies controls): {{ section("clauses_brief", clauses_brief, cap=400) }}
Return the schema object.
```
Gate: analyst confirms each L/I and each control rating; override needs a reason (rule 6); band change and boundary flag acknowledged (R3). Evals: `li_within_one` (L and I within ±1 of the seed label), `control_match_recall` (applicable set ⊇ seed controls), `stale_evidence_detection`, `planned_never_rated` (deterministic, must be 100%), live `override_rate`. Tokens: ≈ 7k in, ≈ 2.2k out ≈ 9.5k.

### 2.5 critic (judge tier)

Purpose and place: state `critic`, after S2 has computed the first score and before anyone reviews (D-22). Reads the evidence ledger for the case and checks four things: every claim sourced, ratings against precedents, the eight rules, evidence recency. It flags; it never changes anything.

Inputs (pack): all proposals for the case run (research, assessment, intake categories) with their Guard 4 results; the S2 result summary as read-only numbers (composite, band, boundary flag: the critic may mention them, it did not produce them); the five precedents' ratings; `rules_check` output (eight booleans with details, computed by code); evidence dates with the code-computed `stale` flag.
Tools: `evidence_ledger` (cap 1: fetch a proposal's full text), `precedent_search` (cap 1), `rules_check` (in-process, called once by the pack builder; the agent may call it again, cap 1).

```python
class CriticFinding(BaseModel):
    type: Literal["unsourced_claim","precedent_deviation","evidence_recency","rule_breach","inconsistency","scope_gap"]
    severity: Literal["low","medium","high"]
    about: list[str] = Field(min_length=1, description="proposal ids or 'ctl:CTL-T01' / 'field:F21' handles")
    statement: str = Field(max_length=320)
    evidence: list[Provenance] = Field(min_length=1, max_length=3)
    deviation: float | None = None             # numeric gap for precedent_deviation
    suggested_action: str = Field(max_length=200)
class CriticChecks(BaseModel):
    claims_checked: int; claims_sourced: int; rules_checked: Literal[8] = 8; rules_kept: int; precedents_compared: int; evidence_items_checked: int
class CriticOutput(AgentOutput):
    findings: list[CriticFinding] = Field(max_length=10)
    checks: CriticChecks
```

System prompt (`critic.system.v1`):
```
You are the critic in a bank's change-risk workbench: a second model that reviews what the research
and assessment steps proposed before a human analyst sees it. You find problems; you do not fix them,
re-rate anything or decide anything. The analyst must respond to each finding.
Check, in this order, and report counts in checks:
1. Sources: each proposal item has a source and the quote matches the source span shown. Report any
   item whose quote does not appear in its source as unsourced_claim (high).
2. Precedents: for each rated control, compare with the ratings of the five closest cases. A gap of
   {{ thresholds.precedent_deviation_flag }} or more is precedent_deviation; state the number.
3. Rules: the eight rules were evaluated by code and are shown with their results. Report any failed
   rule as rule_breach (high). Do not re-derive a rule result; quote the code result.
4. Recency and scope: evidence older than {{ thresholds.stale_evidence_months }} months or whose stated
   scope does not cover the change (geography, domain, product) is evidence_recency.
5. Inconsistency: a value asserted twice with different content across proposals or documents.
6. Scope gap: a domain in scope with no clause and no gap declared, or a control with no decision.
Every finding carries evidence: the span or record you relied on, quoted verbatim. Say what the
analyst should do (explain or adjust), never what the new value should be.
Numbers from the scoring engine are shown read-only; you may cite them, you did not produce them.
{{ skill("compare_precedent") }}
Output one object matching: {{ schema("CriticOutput") }}
```
User template (`critic.user.v1`):
```
Case {{ case.id }} · v{{ case.submission_v }} · engine result (read-only): composite {{ score.composite }} · band {{ score.band }} · boundary {{ score.boundary_flag }}
Proposals in the ledger (id · agent · item · guard4): {{ section("proposals", proposals, cap=4200) }}
Precedent ratings (case · control · rating): {{ section("precedent_ratings", precedent_ratings, cap=600) }}
Rules check (code): {{ section("rules", rules_result, cap=400) }}
Evidence dates and code stale flags: {{ section("evidence_dates", evidence_dates, cap=400) }}
Report findings and checks in the schema.
```
Gate: analyst explains or adjusts each finding (recorded on the finding). Evals: `planted_error_detection` (recall by error type), `finding_precision` (judge-labelled usefulness), `finding_grounding` (deterministic). Tokens: ≈ 7k in, ≈ 1.2k out ≈ 8.5k.

### 2.6 narrative (mid; two modes)

Mode `completeness` (state `completeness`, before review, D-16): lists missing, stale and contradictory items and drafts the one clarification round. Mode `recommendation` (after the analyst's review and the final S2 result, state `review`): drafts the recommendation text, the conditions and the plain-language summary for the owner. Numbers are read-only inputs in both modes.

Inputs (pack, completeness): required field list with filled/empty status (code), submission v1 values with sources, critic findings, research gaps, evidence dates with stale flags, owner answers so far. Inputs (recommendation): the locked S2 result (composite, band, route), confirmed factors and ratings with override reasons, accepted clauses (ids and quotes), critic findings and the analyst's responses, planned controls, recorded policy gaps, clarification answers, the field values; the restricted-terms list is enforced by code, not shown.
Tools: `case_record`, `evidence_ledger` (cap 1 each).

```python
class MissingItem(BaseModel):        field_id: str; why_needed: str = Field(max_length=160)
class StaleItem(BaseModel):          about: str; evidence_date: date; provenance: Provenance
class ContradictoryItem(BaseModel):  statement: str = Field(max_length=240); a: Provenance; b: Provenance
class Question(BaseModel):
    anchor: str = Field(pattern=r"^(F\d{2}|CTL-[A-Z]\d{2})$"); text: str = Field(max_length=280)
    from_finding: str | None = None; keep: bool; drop_reason: str | None = Field(default=None, max_length=160)
class CompletenessOutput(AgentOutput):
    missing: list[MissingItem]; stale: list[StaleItem]; contradictory: list[ContradictoryItem]
    questions: list[Question] = Field(max_length=6); enough_to_assess: bool

class Condition(BaseModel):
    text: str = Field(max_length=200); owner_role: Literal["change_owner","fcrm_ops","fcrm_analyst"]
    due: Literal["before_go_live","at_go_live","3_months","6_months","12_months"]
    derived_from: Literal["overridden_control","planned_control","policy_gap","weak_control"]; ref: str
class Recommendation(BaseModel):
    outcome_suggested: Literal["approve","approve_with_conditions","defer","reject"]
    text: str = Field(max_length=1200); grounded_in: list[RecordRef | str] = Field(min_length=2)
class RecommendationOutput(AgentOutput):
    recommendation: Recommendation; conditions: list[Condition] = Field(max_length=6)
    business_summary: str = Field(max_length=1100)   # code rejects restricted terms; judge checks faithfulness
```

Shared system prompt (`narrative.system.v1`):
```
You are the narrative step of a bank's change-risk workbench. You write for people: the FCRM analyst,
the risk committee and the business owner. You never set or change a number. Likelihood, impact,
ratings, scores and bands are given to you read-only and you repeat them only where the reader is
allowed to see them. You draft; the analyst edits and locks.
In completeness mode: list what is missing (required fields with no value), stale (evidence past
{{ thresholds.stale_evidence_months }} months or out of scope) and contradictory (two sources that
disagree). Draft at most {{ thresholds.max_questions_per_round }} questions, each anchored to a field
or control id, each traceable to a finding or gap. Drop a question the record already answers and say
where it is answered. Decide enough_to_assess honestly: false if a blocking field is missing.
In recommendation mode: write the recommendation in under 180 words from the confirmed inputs; cite the
record or proposal ids you relied on. Suggest one of the four outcomes; the committee or analyst decides.
Draft a condition for every overridden control, planned control, recorded policy gap and any control
rated below 5, each with an owner role and a due horizon. Then write the business summary: what was
decided, why in plain words, what the owner must do by when. The summary must not contain any score,
band, rating, residual, composite, likelihood, impact, clause id or control id.
Every number you repeat must appear in the inputs exactly as given; do not round or recompute.
{{ skill("draft_for_business_reader") }} {{ skill("batch_questions") }}
Output one object matching the schema for the mode: {{ schema(mode_schema) }}
```
(`mode_schema` is a prompt-level constant bound at load time, not a case variable: the loader renders two stable system texts, `narrative.system@1[completeness]` and `narrative.system@1[recommendation]`, each its own cache prefix.)
User templates (`narrative.completeness.user.v1` / `narrative.recommendation.user.v1`):
```
[completeness] Case {{ case.id }} · v{{ case.submission_v }} · required fields {{ required.count }} · filled {{ required.filled }}
Field status (id · label · value or EMPTY · source): {{ section("fields", fields_status, cap=900) }}
Critic findings: {{ section("findings", findings, cap=700) }}   Research gaps: {{ section("gaps", gaps, cap=300) }}
Evidence dates (control · date · stale): {{ section("evidence_dates", evidence_dates, cap=300) }}
Owner answers so far: {{ section("answers", answers, cap=400) }}
Report missing, stale, contradictory and the questions.

[recommendation] Case {{ case.id }} · v{{ case.submission_v }} · locked result (analyst-visible): composite {{ score.composite }} · band {{ score.band }} · route {{ score.route }}
Confirmed factors and ratings (domain · L · I · control · rating · overridden? · reason): {{ section("ratings", ratings, cap=1200) }}
Accepted clauses (id · quote): {{ section("clauses", clauses, cap=800) }}   Planned controls: {{ section("planned", planned, cap=200) }}
Critic findings and analyst responses: {{ section("findings", findings, cap=600) }}   Policy gaps recorded: {{ section("gaps", gaps, cap=200) }}
Clarification answers (question · answer · doc): {{ section("answers", answers, cap=500) }}
Owner-facing facts allowed (go-live, partner, corridor, conditions): {{ section("owner_facts", owner_facts, cap=300) }}
Write the recommendation, conditions and business summary.
```
Gates: completeness → the analyst chooses "ask now" or "proceed", edits and sends the round (one round, D-16); recommendation → the analyst edits and locks (D-19a); the PO summary must pass the restricted-terms check before the pack can be released. Evals: `question_grounding` (each question traces to a finding/gap/empty field: deterministic), `question_dedupe` (dropped questions really are answered: judge), `narrative_faithfulness` (judge rubric §7.3), `number_grounding` (deterministic: every number in the text is in the inputs), `restricted_terms_zero` (deterministic), `conditions_map_to_weak_controls` (deterministic). Tokens: completeness ≈ 4.5k in / 0.8k out; recommendation ≈ 6k in / 1.3k out.

### 2.7 examiner (mid)

Purpose and place: state `oversight`, on demand from Assurance › Ask. Answers a question about one case from the hash-chained audit records only; every sentence cites records; refuses when the records do not hold the answer (D-23).

Inputs (pack): the question; all audit records for the case (seq, type, by, what, ts, and the payload summary) after the chain is verified by code (`GET /verify/{case}` result shown); the versions pinned on the case; the routing rules text in force (pinned version) for questions about routing.
Tools: `audit_log` (cap 2: fetch a record's full payload).

```python
class ExaminerSentence(BaseModel):
    text: str = Field(max_length=320); records: list[RecordRef] = Field(min_length=1, max_length=4)
    quotes: list[Provenance] = Field(min_length=1, max_length=2)   # verbatim from the cited records' `what`/payload
class ExaminerOutput(AgentOutput):
    answered: bool; sentences: list[ExaminerSentence] = Field(max_length=10)
    refusal_reason: str | None = Field(default=None, max_length=240); records_read: int
```

System prompt (`examiner.system.v1`):
```
You answer questions from examiners and auditors about one assessed change, using only the audit
records of that case. The records are an append-only, hash-chained log; code has verified the chain
before you read it. You add nothing from memory, policy knowledge or inference beyond the records.
Rules:
1. Every sentence cites at least one record number and quotes a fragment of that record verbatim.
2. If the records do not contain the answer, set answered=false and say which records you read and
   what is absent. Do not guess, do not summarise what "probably" happened.
3. Repeat numbers, names and dates exactly as the records hold them. Do not round or convert.
4. Keep the order of events as the sequence numbers give it.
5. You may explain a routing or scoring outcome only by citing the record that states it and the
   rule or model version named in that record; never by recomputing.
6. Answer in at most ten sentences, plain language, no opinions on whether the decision was right.
{{ skill("cite_verbatim") }} {{ skill("declare_gap") }}
Output one object matching: {{ schema("ExaminerOutput") }}
```
User template (`examiner.user.v1`):
```
Case {{ case.id }} · chain verified: {{ chain.verified }} ({{ chain.count }} records, head {{ chain.head_hash[:8] }})
Versions pinned: {{ versions | tojson }}
Question: {{ question }}
Audit records (seq · type · by · what · ts): {{ section("records", records, cap=5200) }}
Routing rules {{ versions.routing_v }} (quoted): {{ section("routing", routing_rules, cap=400) }}
Answer in the schema.
```
Gate: none (read-only answer shown to the asker; it is itself logged as an audit record of type `examiner.answered`). Evals: `sentence_record_grounding` (100% of sentences cite an existing record and the quote is verbatim), `unanswerable_refusal_rate`. Tokens: ≈ 6k in, ≈ 0.9k out.

### 2.8 watchlist (cheap)

Purpose and place: state `oversight`, triggered by H1 when policy-service publishes a new jurisdiction list version. Code computes the candidate set (open cases whose geographies intersect the list's added/moved countries); the agent writes the re-flag proposal per case: why, which list line, which agents to re-run. The intersection is deterministic; the agent's value is the per-case explanation and the re-run scope, and it stays an agent so the proposal carries provenance and the analyst acknowledgement is uniform.

Inputs (pack): list diff (added, removed, moved, with list lines), candidate cases (id, title, stage, geographies, change types), corpus clauses that reference the lists (FATF-01/02). Tools: `jurisdiction_list` (cap 1), `case_search` (cap 1).

```python
class ReflagProposal(BaseModel):
    case_id: str; country: str; list_change: Literal["added","removed","moved"]
    statement: str = Field(max_length=240); provenance: list[Provenance] = Field(min_length=1, max_length=2)   # ListRef line + optional ClauseRef
    rerun: list[Literal["research","assessment"]] = Field(min_length=1)
class WatchlistOutput(AgentOutput):
    reflags: list[ReflagProposal] = Field(max_length=20); cases_considered: int
```
System prompt (`watchlist.system.v1`):
```
You are the watchlist step of a bank's change-risk workbench. A new jurisdiction list version has been
published. Code has already selected the open cases whose geographies intersect the countries that were
added or moved. You write one re-flag proposal per candidate case; an FCRM analyst acknowledges each.
Rules:
1. You do not add or remove candidate cases. Every candidate gets exactly one proposal.
2. Each proposal names the country, the change (added, removed, moved) and quotes the list line verbatim
   from the new list version. Add the corpus clause that ties list status to geography risk when shown.
3. Say which agents must re-run: research always (geography clauses may change); assessment only when a
   geography control's evidence in that case depends on list status (for example corridor EDD).
4. Do not rate country risk, do not predict a score or band change, do not comment on the case's merits.
5. One sentence of statement per case, written for the analyst's queue.
{{ skill("cite_verbatim") }}
Output one object matching: {{ schema("WatchlistOutput") }}
```
User template (`watchlist.user.v1`):
```
Jurisdiction list {{ list.from_v }} → {{ list.to_v }} published {{ list.published }}{% if list.fixture %} (synthetic test list){% endif %}
Diff (country · change · list line): {{ section("list_diff", list_diff, cap=400) }}
Candidate cases selected by code (id · title · stage · geographies · types · geography controls with evidence): {{ section("candidates", candidates, cap=900) }}
Clauses referencing the lists: {{ section("clauses", fatf_clauses, cap=300) }}
Write one proposal per candidate.
```
Gate: analyst acknowledges each re-flag; acknowledgement queues the re-runs. Eval: `reflag_recall` (every code candidate has a proposal) and `reflag_grounding`. Tokens: ≈ 2.2k in, 0.5k out.

### 2.9 policy_gap (mid)

Purpose and place: state `oversight`, weekly or when an analyst records a gap. Groups recorded gaps across cases, confirms the corpus still lacks coverage (a fresh `policy_search` per gap), and drafts a suggestion for the policy owner: topic, affected cases, nearest existing clauses, suggested home standard. It never drafts policy text that could be mistaken for policy; it drafts a one-sentence scope statement explicitly labelled as a suggestion.

Inputs (pack): open gap records (topic, case ids, the research `Searched` block), nearest clauses per gap from a fresh search (top 5 with scores), corpus catalogue. Tools: `policy_search` (cap 3), `case_search` (cap 1).

```python
class GapSuggestion(BaseModel):
    topic: str = Field(max_length=120); cases: list[str] = Field(min_length=1); searched: Searched
    nearest_clauses: list[dict[str, float | str]] = Field(max_length=5)      # {clause_id, score}
    suggested_standard: str = Field(max_length=80); scope_suggestion: str = Field(max_length=240)
    provenance: list[Provenance] = Field(min_length=1)   # the gap record(s) quoted
class PolicyGapOutput(AgentOutput):
    suggestions: list[GapSuggestion] = Field(max_length=10)
```
System prompt (`policy_gap.system.v1`):
```
You are the policy-gap step of a bank's change-risk workbench. Analysts have recorded topics for which
the research step found no clause in the policy corpus. You confirm whether the corpus still lacks
coverage and, where it does, draft a suggestion for the policy owner. You do not write policy.
Rules:
1. For each gap, read the nearest clauses retrieved for it. Coverage exists if a clause's text addresses
   the topic; then quote that clause, state that the gap is closed, and output no suggestion for it.
2. Group gaps that concern the same topic across cases into one suggestion listing every case id.
3. Name the nearest clauses with their scores exactly as given; do not re-rank or invent clause ids.
4. Suggest the internal standard that should own the topic, chosen from the corpus catalogue shown.
5. The scope suggestion is one sentence and begins "Suggested scope (not policy): ". It may not quote or
   imitate regulatory text, cite a regulation that is not in the catalogue, or set a control or rating.
6. Each suggestion quotes the gap record(s) it is based on.
Corpus catalogue ({{ versions.corpus_v }}): {{ config("corpus.catalogue") }}
{{ skill("declare_gap") }} {{ skill("cite_verbatim") }}
Output one object matching: {{ schema("PolicyGapOutput") }}
```
User template (`policy_gap.user.v1`):
```
Open gap records (id · case · topic · statement · searched queries and filters): {{ section("gaps", gaps, cap=1200) }}
Fresh nearest clauses per gap (gap id · clause id · source · score · text): {{ section("nearest", nearest_clauses, cap=1800) }}
Write the suggestions, or close gaps that are now covered.
```
Gate: policy owner reviews (served by the analyst role until a `fcrm_policy_owner` role exists; proposed). Eval: `suggestion_acceptance` (live label), `coverage_check_correctness` (no suggestion where a labelled clause exists). Tokens: ≈ 3.5k in, 0.9k out.

### 2.10 Per-case token picture (demo case, first run through decide)

intake 4–5 windows ≈ 24k · screening 1.8k · research 8k + re-run 5k · assessment 9.5k + re-run 6k · critic 8.5k · narrative 5.3k + 7.3k · examiner (oversight) 7k · total ≈ 82k of the 120k case budget before the oversight agents, with Guard 4 retries adding up to one tier budget each. The walkthrough's hard-coded 16.9k is not a target; §8 says what we report instead.

---

## 3. Context engineering

### 3.1 JobPayload (H1 → H2, small) and ContextPack (H2-built, snapshotted)

```python
class JobPayload(BaseModel):            # published on agent.run; ≤ 4 KB
    job_id: str; case_id: str; agent: AgentName; mode: str | None; attempt: int = 1
    submission_v: int; changed_fields: list[str] = []; new_doc_ids: list[str] = []
    page_window: tuple[int, int] | None = None; doc_id: str | None = None          # intake shards
    versions: PinnedVersions          # scoring_v, corpus_v, controls_v, fieldset_v, prompts_v, routing_v, jurisdiction_v
    refs: dict[str, str] = {}        # e.g. research_proposal_id, score_result_id, question_id
    budget: BudgetSlice               # remaining case tokens, tier limit, wall_s
    trace: dict[str, str]             # W3C traceparent for the case trace
    requested_by: str                 # user id or "h1"

class PackSection(BaseModel): name: str; text: str; tokens: int; truncated: bool; source_hashes: list[str]; cacheable: bool
class ContextPack(BaseModel):
    pack_hash: str; agent: AgentName; mode: str | None; case_id: str; submission_v: int; versions: PinnedVersions
    system_text: str; user_text: str; sections: list[PackSection]; cache_points: list[int]   # char offsets in user_text, ≤ 2
    tool_scope: ToolScope             # case_id and versions every tool call must match
    tokens_total: int; built_at: datetime; builder_version: str
```

### 3.2 ContextPackBuilder interface and the nine builders

```python
class ContextPackBuilder(Protocol):
    agent: AgentName
    async def build(self, payload: JobPayload, tools: ToolClients, prior: PriorPack | None) -> ContextPack: ...
```
Steps common to all builders, in order: (1) load the AgentSpec and prompt templates for `versions.prompts_v`; (2) fetch case record at `submission_v` through `case_record` (role "agent", so the response has no score fields unless the builder is narrative.recommendation or critic, which read the S2 result from `refs.score_result_id` via H1-provided payload data, not from case-service); (3) run the deterministic retrieval plan (per-builder, §3.5) through the retrieval cache; (4) render sections with caps and the ordering below; (5) render system and user prompts; (6) place cache points; (7) hash and snapshot. The builder never calls a model.

Section order inside the user prompt is fixed: identity line (case id, version, types, domains) → pinned versions → re-run delta (if any) → case fields → the agent's primary evidence (document pages / clauses / controls / proposals / records) → secondary evidence (precedents, jurisdictions, evidence dates) → instructions line. Stable, large, shared-across-reruns material comes first inside the user prompt so the optional second cache point can cover it.

Per builder, what is assembled (the "exactly what the agent sees"):
- `ScreeningPackBuilder`: fields F01–F06 + type fields; R4 text; code facts; 3 precedent titles.
- `IntakePackBuilder`: field set rows for the candidate types (synonyms from field set v6); the document window text with `p.N l.M:` anchors; prior extraction ids. Page ranking: BM25 (rank-bm25) over page texts with the field synonym list as the query; pages ordered by rank but rendered in document order; `page_window` from the payload overrides.
- `ResearchPackBuilder`: fields; 12 clauses (rank fusion already done by policy-service; the builder keeps the top 12 after de-duplicating by `clause_id`); 5 precedents with control ratings for in-scope domains; jurisdiction lines; controls brief; corpus catalogue.
- `AssessmentPackBuilder`: fields; scale definitions; controls for in-scope domains (all rows, never truncated; 24 max); evidence (inventory evidence + document spans found by a deterministic control-synonym search over the case Markdown); precedent ratings; research clauses brief if `refs.research_proposal_id` present.
- `CriticPackBuilder`: all proposals of this run (compact: id, agent, item type, key fields, quote, source, guard4 status); S2 summary; precedent ratings; `rules_check` result; evidence dates.
- `NarrativePackBuilder` (two modes): see §2.6; the recommendation mode receives the S2 result and ratings from the payload refs (H1 copies them into `refs` as ids; the builder fetches via `evidence_ledger`).
- `ExaminerPackBuilder`: verified chain summary; all records compact; routing rules text; versions.
- `WatchlistPackBuilder`: list diff; code-selected candidates; FATF clauses.
- `PolicyGapPackBuilder`: open gaps; fresh nearest clauses per gap; catalogue.

### 3.3 Token caps, ordering and truncation (proposed values; `ai/guards/thresholds.yaml: pack_section_caps`)

Tokens are counted with the Bedrock `CountTokens`-equivalent heuristic `len(text)/3.6` at build time (fast, conservative) and corrected with the real `input_tokens` after the call (the correction is stored on the pack snapshot for the next build's calibration). Caps per section are listed in the user templates above; totals: cheap packs ≤ 4.6k, mid ≤ 7.5k, strong/judge ≤ 9k, leaving the output and one tool round-trip inside the tier budget.

Truncation policy per section kind:
- `fields`: never truncated (bounded by field set v6: ≤ 18 required + type fields).
- `document`: whole pages only, in document order; a page that does not fit is deferred to a continuation window (never cut mid-page). The section header says `pages shown: 1–6 of 14; 7–14 in a later window`.
- `clauses`, `precedents`, `evidence`, `records`, `proposals`: ranked lists cut at the cap by rank; the header says `showing 9 of 12 (3 omitted by budget: ids …)` so the agent can request an omitted item through the tool (within the call cap) rather than invent it.
- `controls`: never truncated (the inventory for in-scope domains is small by design); if it would exceed the cap the builder raises `PackOverflow` and H1 splits the job by domain.
- Free text (owner answers, findings) is cut at the cap with a visible `[truncated]` marker and the item id, never silently.
Every truncation is recorded as `truncated: true` on the section and counted in the model-call record (`pack_truncations`), so evals can correlate truncation with misses.

### 3.4 Bedrock prompt caching strategy

Bedrock Converse supports explicit cache points only; the minimum cacheable prefix is model-dependent (`cache_min_prefix_tokens` in tiers.yaml). Strategy:
- The **system prompt** (role text + skills + schema) is identical for every run of an agent version. For strong/mid/judge tiers it is 1.3–2.2k tokens, above the 1024 minimum: cache point 1 after the system prompt. Expected hit rate is high in bursts (research ∥ assessment on the same case, critic shortly after) and across cases within the 5-minute TTL during a demo or an eval run.
- A **second cache point** goes at the end of the stable, case-level block of the user prompt (fields + primary evidence) when the same pack prefix will be reused within minutes: the Guard 4 retry (same prefix plus the error feedback), and the intake continuation windows (same field set). Re-runs after a clarification round happen hours later and do not rely on Bedrock caching; they rely on our retrieval cache (§3.5).
- Cheap tier: Haiku-class minimum is 4096 tokens; the cheap system prompts are ≈ 0.9k, so the builder emits **no** cache point for cheap-tier calls unless the prefix exceeds the minimum (it never does by design); cheap prompts are kept short instead.
- Implementation: the builder marks offsets; the runtime inserts Pydantic AI's cache marker (`CachePoint` in `pydantic_ai.messages`, verify name) at those offsets, or, if the installed version lacks it, sets `BedrockModelSettings` to add `cachePoint` blocks. `cache_read_tokens`/`cache_write_tokens` from `RunUsage` are recorded per call; the token report (§8) shows hit rates per agent, and `test_cache_prefix_stable` asserts that two renders of the same system prompt are byte-identical (no timestamps, no case ids in the system text).

### 3.5 Version pinning and retrieval caching for re-runs

Pinning: `PinnedVersions` travels in the payload, is rendered into the pack's identity line, is sent as filters on every tool call (`corpus_v`, `controls_v`, `fieldset_v`, `jurisdiction_v`), and is checked by the tool client (`scope_rule`): a result tagged with another version is rejected before the model sees it. The pack snapshot stores the versions, so replaying `pack_hash` reproduces the inputs even after config changes. The pin set now includes `routing_v` and `jurisdiction_v` (fixing the §7 omission noted in the build plan).

Retrieval cache (`fcrm_harness.retrieval_cache`; proposed): key = sha256(tool, canonical JSON of input, versions in scope); value = tool output, hit count, created_at; TTL 24 h for policy/precedent/control lookups, no TTL for `document_reader` pages (immutable per doc id). On a clarification re-run, H1 sets `changed_fields` and `new_doc_ids`; the builder (1) loads the prior pack for (case, agent, submission_v−1), (2) recomputes the retrieval plan, (3) re-queries only the plan entries whose query text depends on a changed field (the plan records `depends_on_fields` per query), (4) reuses the rest from the cache, (5) renders a "re-run delta" line. Expected effect: the research re-run on the demo case (two changed fields, one new document) re-queries 2 of 6 clause queries and reuses 5 precedents.

---

## 4. Guardrails package (`src/packages/guardrails/fcrm_guardrails`)

### 4.1 Common interface

```python
class GuardName(StrEnum): input_scan="input_scan"; permit="permit"; budget="budget"; output_shape="output_shape"; rules="rules"; critic="critic"; human_gate="human_gate"
class GuardStatus(StrEnum): passed="passed"; masked="masked"; annotated="annotated"; retry="retry"; blocked="blocked"
class GuardResult(BaseModel):
    guard: GuardName; status: GuardStatus; summary: str; details: dict[str, Any] = {}
    ledger_items: list[LedgerAnnotation] = []        # per-item annotations written next to proposals
    audit_type: str                                  # e.g. "guard.output"
    duration_ms: int
class GuardContext(BaseModel, arbitrary_types_allowed=True):
    payload: JobPayload; pack: ContextPack | None; output: AgentOutput | None; usage: RunUsage | None
    attempt: int; deps: GuardDeps                    # opa client, budgets repo, corpus lookup, doc store, chain reader
class Guard(Protocol):
    name: GuardName; stage: Literal["pre","post","state"]
    async def run(self, ctx: GuardContext) -> GuardResult: ...
class GuardChain:                                    # runs the configured subset in the configured order; stops on blocked
    async def run_pre(self, ctx) -> list[GuardResult]; async def run_post(self, ctx) -> list[GuardResult]
```
Every `GuardResult` is written twice: to the ledger (attached to the job and, for Guard 4, to each proposal item) and to the audit chain through S4 (`POST /records` with `type=audit_type`). H2 never writes to Mongo directly; it posts to S4 and S1.

### 4.2 Guard 1 · InputScanGuard (pre; H3 after extraction, and H2 on every pack)
Inputs: canonical Markdown of each new document (H3), owner free text and every pack section (H2). Checks: (a) injection pattern list (`injection_patterns.txt`: "ignore previous", "you are now", "system:", tool-call look-alikes, base64 blobs > 200 chars, zero-width characters, HTML comments, `<|` tokens) scored by weighted hits; (b) a small classifier is **not** built in the hackathon window: the pattern list plus a Haiku-class yes/no check on the top-scoring 2 KB window only when the pattern score is in the amber band (proposed; counted against the case budget, cheap tier); (c) presidio `AnalyzerEngine` with the entity list in `pii_entities.yaml` (US_SSN, CREDIT_CARD, PHONE_NUMBER, EMAIL_ADDRESS, IBAN_CODE, US_BANK_NUMBER, US_PASSPORT, US_DRIVER_LICENSE, custom DATE_OF_BIRTH, and PERSON only inside signature blocks matched by the regex) → `AnonymizerEngine` replaces with `<PII:TYPE:n>` tokens. Business names, role names and the change owner's name are not masked (F01 depends on them). Failure behaviour: pattern score ≥ `injection_score_block` → `blocked`: the file is quarantined (status in S1), the analyst is flagged, the job is not dispatched; PII found → `masked` (the masked text is the canonical text agents see; the original stays in MinIO for humans). Writes: `guard.input` audit record per file/pack with counts, never the matched PII.

### 4.3 Guard 2 · PermitGuard (pre; H2 before each tool call and once for the agent's whole allow-list at job start)
Input to OPA: `{"input": {"agent": "research", "tool": "policy_search", "case_id": "...", "mode": "...", "role": "agent", "read_only": true}}` → `data.tools.allow`. Also enforced in code: `ToolClient` refuses any tool not in `AgentSpec.tools` (Python allow-list fallback, BP §11), refuses a `case_id` different from the payload's, and refuses non-GET backing calls. Decisions cached in Redis 60 s keyed `agent·tool`. Failure: `blocked` for that tool call (the agent receives a tool error "not permitted" and must proceed without it); if the job-start check fails for any tool in the spec, the job is blocked and H1 is told. Writes: `guard.permit` with the allowed list (one record per job) and `guard.permit.denied` per refusal.

### 4.4 Guard 3 · BudgetGuard (pre in H1 and H2; also wraps the call)
H1: before dispatch, reserves `tier.budget.tokens × (1 + retries)` against `fcrm_harness.budgets[case_id]` (`limit 120000, used, reserved, by_agent{}`); if `used + reserved > limit` the job is not dispatched and a human gate `budget_exhausted` opens with the raw inputs. H2: wraps the model call with `UsageLimits(total_tokens_limit=tier.tokens, request_limit=1 + tool_call_cap)` and `asyncio.timeout(tier.wall_s)` per attempt; after the call it reports actual usage to H1 (`agent.done.usage`) which releases the reservation and adds the actual. Fallback order is the tier's `FallbackModel(primary, fallback)` for transport errors on attempt 1 and the fallback model alone on attempt 2. Failure: `UsageLimitExceeded` or timeout → attempt 2 on the fallback model with a halved `max_tokens` hint; a second failure → `blocked`, H1 opens the human gate (F6). Writes: `guard.budget` with reserved/used and the fallback decision.

### 4.5 Guard 4 · OutputShapeGuard (post; H2)
Checks, all deterministic: (1) Pydantic validation of the declared output model (`extra="forbid"`); (2) every `SourceRef` resolves: `DocRef` → document in the pack, page ≤ pages, lines within the page; `ClauseRef` → clause exists in `corpus_v`; `RecordRef` → `seq ≤ chain length` for the case; `CaseRef` → case and field exist; `ControlRef` → control in `controls_v`; `ListRef` → list version exists and holds the country; `OwnerRef` → answer exists; (3) every `quote` exists verbatim in the referenced span: normalisation = NFKC, collapse whitespace, map curly quotes/dashes/ellipsis to ASCII, strip leading/trailing ellipsis; case-sensitive contiguous substring of the span text (document span ± `quote_line_tolerance` lines; whole clause text; record `what` + payload text); (4) confidence in [0,1]; (5) agent-specific invariants: `ClauseProposal.quote == provenance.quote`; `PrecedentMatch.similarity` equals the tool result; `ControlProposal` planned/rating rule; `ExaminerSentence.records ⊆ chain`; `RecommendationOutput.business_summary` contains no restricted term (regex list, word-boundary, case-insensitive) and every number token in `recommendation.text`/`business_summary` appears in the inputs' number set (`number_grounding`). Failure: `retry` once on the fallback model with a feedback message listing each failing item and the rule it broke ("drop any item you cannot source"); if the retry fails → `blocked`, `agent.done{status: failed_output}` and the human gate (F6). Items that fail only check (3) on the retry are **dropped**, not fixed, and listed in `notes_for_analyst` ("dropped: unsourced value for F22"), matching flow node I6D. Writes: per-item `LedgerAnnotation{item_id, check, ok}` and one `guard.output` record ("15 of 15 sourced · 0 rejected").

### 4.6 Guard 5 · RulesGuard (state; H1 before every state change, H2 on outputs for rules 1, 7, 8)
Runs the eight pure functions below with the relevant inputs; any failing rule → `blocked` and the state change is refused with the rule id in the error. Writes: `guard.rules` with the eight booleans.

```python
# fcrm_guardrails/rules/__init__.py  — pure, synchronous, no I/O, fully typed; one test module per rule plus hypothesis property tests
class RuleResult(BaseModel): rule_id: Literal["R-1".."R-8"]; name: str; ok: bool; detail: str = ""
def no_agent_wrote_a_score(proposals: Sequence[LedgerProposal]) -> RuleResult          # no proposal item has keys in {score, composite, residual, band, retention}; FactorProposal L/I are inputs, allowed
def no_bulk_accept(gate_events: Sequence[GateDecided]) -> RuleResult                     # every gate.decided carries exactly one item_id; no two from the same actor within 300 ms
def po_view_has_no_restricted_fields(response: Mapping[str, Any], role: Role) -> RuleResult  # for role fcrm_po: recursive key set ∩ RESTRICTED_KEYS == ∅ and no restricted term in string values
def analyst_never_edited_submission(versions: Sequence[SubmissionVersion]) -> RuleResult  # every version.author_role == "fcrm_po"; analyst actions are clarifications, not edits
def closed_case_untouched(state: CaseState, event: CaseEvent) -> RuleResult               # state in {closed, decided_closed} → only read/examiner/condition_update events allowed
def override_has_reason(override: OverrideEvent) -> RuleResult                           # reason stripped length ≥ 20 and not equal to the proposed value's rationale
def planned_control_not_scored(scoring_input: ScoringInput) -> RuleResult                # every control with planned=True has rating None and is absent from the weighted set
def policy_quoted_not_generated(clauses: Sequence[ClauseProposal], corpus: ClauseLookup) -> RuleResult  # every quote is a verbatim substring (normalised) of corpus[clause_id].text
def check_all(ctx: RulesContext) -> list[RuleResult]                                      # runs those whose inputs are present; missing inputs → ok=True, detail="not applicable"
```

### 4.7 Guard 6 · CriticGuard (state; H1)
Not a check but a hook: after S2 returns the first score for a run, H1 dispatches the critic job; the state machine cannot enter `completeness` until `agent.done` for the critic has landed or the critic hit F6. Findings are written to the ledger as proposals of type `CriticFinding`; each must be answered by the analyst (explain or adjust) before `review` can complete (checked by the four release checks, D2). Writes: `critic.findings` record with counts.

### 4.8 Guard 7 · HumanGate (state; H1)
`Gate{gate_id, case_id, kind (po_confirm_category | po_confirm_value | analyst_fast_lane | analyst_clause | analyst_rating | analyst_finding | analyst_ack_band | analyst_lock | committee_vote | failed_step), item_id, opened_at, decided_at, decided_by, decision, reason}`. H1 waits on `gate.decided`; the UI posts to S1, S1 publishes. A gate with `kind=analyst_rating` and a changed value requires `reason` (rule 6). Writes: `gate.decided` records, one per item (rule 2).

---

## 5. Agent-worker runtime (H2, `src/workers/agent-worker`)

### 5.1 Loop

```
consume agent.run (FastStream, RabbitMQ, prefetch 2 per replica, manual ack)
  claim job atomically in fcrm_harness.agent_jobs (status queued→running, worker_id, lease 2·wall_s+30 s)  — a lost worker's lease expires and the job is re-queued once
  open OTel span agent.run (parent = payload.trace)
  spec = registry.agent(payload.agent); tier = tiers[spec.tier]
  pre-guards: InputScanGuard(pack text) → PermitGuard(job allow-list) → BudgetGuard(reservation present)      [blocked → publish agent.done{status: blocked_<guard>}; ack]
  pack = builder.build(payload, tools, prior)  → snapshot to MinIO ai-packs/{case_id}/{pack_hash}.json; POST ledger pack ref
  attempt 1: model = FallbackModel(Bedrock(tier.primary), Bedrock(tier.fallback)); result = await agent.run(pack.user_text, deps, usage_limits, model_settings) under asyncio.timeout(tier.wall_s)
  post-guards: OutputShapeGuard → RulesGuard(rules 1,7,8)
  if OutputShapeGuard.status == retry and attempt == 1:
      attempt 2: model = Bedrock(tier.fallback); user_text = pack.user_text + feedback block; same guards
  on pass: POST proposals to S4 ledger (one proposal per item, with prompt_id@version, rendered hash, pack_hash, model_id, attempt); record model_calls; publish agent.done{status: ok, proposal_ids, usage, guard_results}
  on final failure: publish agent.done{status: failed_output | failed_budget | failed_model, error, pack_hash}; H1 opens gate failed_step
  ack; close span
```
A redelivered message with an already-claimed job id is acked without work (idempotency by `job_id`). Jobs that fail twice are parked in `agent_jobs.status=dead` and published to `agent.dead` (the DLQ the AEO runner lacked).

### 5.2 Model wiring and structured output

```python
provider = BedrockProvider(region_name=settings.aws_region)                     # credentials from the default AWS chain
def model_for(tier: Tier, attempt: int) -> Model:
    primary, fallback = BedrockConverseModel(tier.primary, provider=provider), BedrockConverseModel(tier.fallback, provider=provider)
    return FallbackModel(primary, fallback) if attempt == 1 else fallback
agent = Agent(model_for(tier, attempt), output_type=ToolOutput(spec.output_model, name=f"{spec.name}_output"),
              instructions=pack.system_text, deps_type=AgentDeps, retries=0, tools=registry.tool_functions(spec.tools, scope=pack.tool_scope))
result = await agent.run(pack.user_text, deps=deps, usage_limits=UsageLimits(total_tokens_limit=tier.tokens, request_limit=1 + spec.tool_call_cap),
                         model_settings=ModelSettings(temperature=0, max_tokens=tier.max_tokens, timeout=tier.wall_s))
```
Structured output uses the tool-call mode (`ToolOutput`) because Bedrock Converse has no native JSON mode for Anthropic models; `retries=0` so that Pydantic's own retry never runs on the primary model: the retry policy is ours (Guard 4, fallback model). An `@agent.output_validator` is not used; Guard 4 does the validation so that its result is logged uniformly. Tool functions are thin async wrappers around `ToolClient` with the permit check inside; each returns the tool's output model serialised compactly; a permit refusal returns a tool error string the model sees.

### 5.3 Token accounting record (`fcrm_harness.model_calls`, one per attempt)

```python
class ModelCallRecord(BaseModel):
    call_id: str; job_id: str; case_id: str; agent: AgentName; mode: str | None; attempt: int
    tier: str; model_id: str; fallback_used: bool
    prompt_set_v: str; prompt_ids: dict[str, str]        # {"system": "research.system@1", "user": "research.user@1"}; rendered_hash: str
    pack_hash: str; pack_tokens_est: int; pack_truncations: int
    input_tokens: int; output_tokens: int; cache_read_tokens: int; cache_write_tokens: int; requests: int; tool_calls: int
    cost_usd: Decimal                                     # from tiers.yaml prices; 0 until the lead fills them
    latency_ms: int; status: Literal["ok","retry","failed_output","failed_budget","failed_model"]
    guard_results: list[GuardResult]; ts: datetime; worker_id: str; git_sha: str
```
`RunUsage` fields used: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `requests`, `tool_calls` (verify names on day 1).

### 5.4 OpenTelemetry

Pydantic AI instrumentation is on (`Agent.instrument_all()` with `InstrumentationSettings(include_content=False)` so no case text leaves to Logfire). Spans: `agent.run` (root per job) → `guard.input`, `guard.permit`, `guard.budget`, `context.build` (children `tool.<name>` for retrieval), `model.call` (Pydantic AI's `gen_ai.*` spans), `guard.output`, `guard.rules`, `ledger.write`. Attributes on `agent.run`: `fcrm.case_id, fcrm.job_id, fcrm.agent, fcrm.mode, fcrm.attempt, fcrm.tier, fcrm.model_id, fcrm.fallback_used, fcrm.prompt_set_v, fcrm.prompt_ids, fcrm.pack_hash, fcrm.pack_tokens, fcrm.pack_truncations, gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, fcrm.cache_read_tokens, fcrm.cache_write_tokens, fcrm.cost_usd, fcrm.latency_ms, fcrm.status, fcrm.guard.<name>.status, fcrm.versions.<k>`. The case trace id is the `traceparent` S1 minted at `case.created` (the walkthrough's `0af3c1e9d7b24c6e`). The tracer fix from E (no-op exporter outside the cluster) is applied: the exporter is chosen by `OTLP_EXPORTER_ENDPOINT` presence only.

---

## 6. Memory and the learning loop

### 6.1 Collections and fields (proposed names; databases per service as decided)

| Kind | Collection | Key fields |
|---|---|---|
| Working | (none) | `JobPayload` on the bus; `ContextPack` snapshot in MinIO `ai-packs/` + `fcrm_harness.context_packs {pack_hash, case_id, agent, mode, submission_v, versions, sections[{name,tokens,truncated,source_hashes}], storage_key, tokens_total, built_at}` |
| Short-term | `fcrm_harness.case_runs` | event-sourced: `{case_id, seq, type (job.dispatched, job.done, guard.result, gate.opened, gate.decided, state.changed, score.requested, score.returned), payload, ts}` |
| Short-term | `fcrm_harness.agent_jobs` | `{job_id, case_id, agent, mode, status, attempt, worker_id, lease_until, payload, result_ref}` |
| Short-term | `fcrm_harness.budgets` | `{case_id, limit, used, reserved, by_agent{agent:{used,calls}}, exhausted_at}` |
| Short-term | `fcrm_audit.proposals` (evidence ledger) | `{proposal_id, case_id, submission_v, run_id, agent, mode, job_id, attempt, item_type, item (schema object), provenance_checks[], prompt_ids, rendered_hash, pack_hash, model_id, status (proposed/accepted/overridden/removed/dropped/superseded), decided_by, decided_at, reason, human_value, created_at}` |
| Short-term | `fcrm_audit.audit_records` | `{case_id, seq, type, by, what, payload_hash, prev_hash, hash, ts}` (insert-only user, schema validation) |
| Long-term | `fcrm_policy.precedent_index` + S3 Vectors `precedent_cases` | `{case_id, title, unit, change_types, domains, geographies, outcome, closed_date, factors{domain:{L,I}}, control_ratings{ctl:rating}, conditions_count, versions, embedding_id}`; written by H1's oversight core at `case.closed` |
| Long-term | `fcrm_policy.corpus_versions, clauses, controls, jurisdiction_list_versions, field_set_versions` | versioned reference data (seed P7) |
| Long-term | `fcrm_audit.proposals` with `status in (overridden, removed, dropped)` | the override history; a Mongo view `overrides_v` projects `{case_id, agent, mode, item_type, item_id, proposed, human_value, reason, prompt_ids, model_id, decided_at}` |
| Learning | `fcrm_harness.eval_runs` | mirror of `/evals/results/*/index.json` rows `{run_id, ts, agent, prompt_ids, prompt_set_v, model_id, dataset, metrics[], tokens, cost_usd, git_sha, gate}` for the Assurance › Agent quality screen |
| Learning | `fcrm_harness.retrieval_cache` | §3.5 |
| Learning | `fcrm_harness.model_calls` | §5.3 |

### 6.2 Override rate as the live signal (D-71)
`override_rate(agent, window) = overridden / (accepted + overridden)` over `overrides_v` and `proposals`, per agent, per `prompt_ids`, per item type; computed by a small aggregation in S4 (`GET /metrics/overrides?agent=&since=`) and shown in Assurance › Overrides and Agent quality. A week-on-week rise of more than 5 points on any agent opens a "prompt review" item (recorded as an audit record of type `quality.review_opened`).

### 6.3 Overrides become eval rows
`evals/runners/harvest_overrides.py` (run by `make evals-harvest`, and weekly): reads `overrides_v` since the last harvest watermark; for each override loads the `pack_hash` snapshot and writes a candidate row to `/evals/datasets/<agent>/harvested.pending.jsonl`: `{row_id, case_id, agent, mode, pack_ref, proposed: item, expected: human_value, reason, label_source: "analyst_override", labeller, decided_at, status: "pending_review"}`. An analyst confirms the label in Assurance › Overrides ("use as test case"), which flips `status` to `approved` via S4; `harvest_overrides.py --promote` moves approved rows into `<agent>/harvested.jsonl`, which the runner includes. Pending rows are never used for gating. Demo rehearsal overrides (the CTL-T01 7.6 → 4.5 case) become the first harvested rows on day 4.

### 6.4 From eval run to candidate prompt version to promotion
1. A subagent writes `prompts/<agent>/<id>.v<N+1>.md` (changelog `reason`, `eval_run: pending`). `MANIFEST.current` still points at vN.
2. `make evals AGENT=<agent> CANDIDATE=<id>@<N+1>` runs the agent's datasets twice if no baseline exists for the current model/data fingerprint, otherwise once (candidate) and loads the baseline from `/evals/results/baseline/<agent>.json`.
3. `evals/runners/gate.py` compares: **promotion requires primary_metric(candidate) ≥ primary_metric(baseline)** and every `gated_metric` ≥ its threshold; LLM-judge metrics are compared with k = 3 votes per row and a 0.01 tolerance to absorb judge noise; deterministic metrics have no tolerance. Output: `PASS`/`FAIL` with per-metric deltas (reuses the `compare_reports` shape from evoq_evals `regression.py`).
4. On PASS the subagent sets `eval_run: <run_id>`, moves `MANIFEST.current`, bumps `set_version`, runs `make locks`, commits prompt + results together (D-91). Running cases keep their pinned `prompts_v`; new cases get the new set.
5. The pre-commit hook (`/ops/hooks/prompt_gate.py`) refuses a commit that changes `MANIFEST.current` without a `PASS` results file whose `prompt_hash` matches the new file's sha256 and whose `git_sha` is an ancestor of HEAD.

---

## 7. Evals

### 7.1 Tree

```
/evals
  README.md                      metrics, thresholds, how to run, how results are read by the app
  datasets/
    <agent>/<name>.jsonl         rows (schema below); README.md per agent with provenance of the rows
    <agent>/harvested.jsonl, harvested.pending.jsonl
    fixtures/                    tool snapshots (seed-backed): clauses.json, controls.json, precedents.json, jurisdictions.json, docs/<case>/*.md
  redteam/injection-50.jsonl  redteam/pii-20.jsonl  redteam/README.md
  metrics/                       one module per metric; each is a pydantic_evals Evaluator subclass with `name`, `kind` (deterministic|llm_judge), `threshold`, `higher_is_better`
    citation_validity.py  field_extraction_accuracy.py  source_span_accuracy.py  category_f1.py  gap_question_precision.py
    clause_recall.py  clause_precision.py  gap_honesty.py  li_within_one.py  control_match_recall.py  stale_evidence_detection.py
    planted_error_detection.py  finding_precision.py  question_grounding.py  narrative_faithfulness.py  number_grounding.py
    restricted_terms.py  sentence_record_grounding.py  refusal_rate.py  fast_lane_precision.py  reflag_recall.py  suggestion_acceptance.py
    base.py                      FcrmMetric(Evaluator): MetricResult shape {name, score, passed, total, threshold, gate, flags[], evidence[], kind}
  judge/judge.py                 Pydantic AI judge agent on {{MODEL_JUDGE}}; RubricVerdict schema; k-vote; cached
  judge/rubrics/*.md             narrative_faithfulness, finding_usefulness, question_dedupe, judge_calibration
  runners/
    run_agent.py                 `uv run evals run <agent> [--dataset] [--candidate id@v] [--cached]`
    run_all.py  gate.py  harvest_overrides.py  report.py  token_report.py
    offline_runtime.py           runs fcrm_agents with SeedBackedTools, FakeOPA, InMemoryLedger; real Bedrock or cached model
  results/
    baseline/<agent>.json        frozen reference per agent (updated only by promotion)
    <run_id>/<agent>.json, <run_id>/index.json, <run_id>/report.html, trend.jsonl
  tests/test_metrics.py          unit tests for every deterministic metric on hand-made rows (golden metric tests, as in evoq_aidlc_mvp)
```

### 7.2 Datasets (derived from seed P7 and the demo case; sizes are for the 6-day window)

Row schema (all datasets): `{row_id, agent, mode, inputs: {case_id, submission_v, payload_overrides, fixture_refs}, expected: {...}, metadata: {source: seed|demo|synthetic|harvested, difficulty, tags[]}}`. Inputs are materialised by the offline runtime through the same `ContextPackBuilder`, so evals exercise the real pack.

| Dataset | Rows | How derived | Expected fields | Example row |
|---|---|---|---|---|
| `intake/extraction-20` | 20 cases × ~15 fields ≈ 300 field rows (6 docs/case max) | gen_seed extension writes synthetic business-case and contract Markdown per case with planted values at known `p:l` for the case's required fields (field set v6); the demo case's two documents are hand-written to match the seed's 15 values | `fields[{field_id, value, doc_id, page, line_start, line_end}]`, `categories[]`, `absent_fields[]` | `{"inputs": {"case_id": "FCRM-2026-0151", "doc_id": "business_case_remittance_MX", "page_window": [1,6]}, "expected": {"fields": [{"field_id": "F05", "value": "$48M year 1", "page": 6, "line_start": 14, "line_end": 14}], "categories": ["new_geography","new_product"], "absent_fields": ["F10"]}}` |
| `research/clauses-40` | 40 cases | labels = clause ids whose source `used_for` domain and the case's change types match (deterministic labelling from the corpus catalogue and the four internal standards), hand-checked for 10 cases; demo case labelled with the four seed clauses | `relevant_clause_ids[]`, `k: 12` | `{"inputs": {"case_id": "FCRM-2026-0151", "submission_v": 2}, "expected": {"relevant_clause_ids": ["FFIEC-TPPP-04","FCRM42-3.1","TP71-2.3","OFAC-1.2"]}}` |
| `research/gaps-12` | 12 | topics absent from corpus v14 (cash-pickup agents, crypto custody, BNPL) planted into case descriptions | `expected_gap_topics[]`, `forbidden_clause_ids[]` | — |
| `assessment/ratings-80` | 80 cases (all seed cases have L/I and control ratings as labels) | case brief generated from seed fields; controls and evidence from the inventory with the seed's `evidence_date` | `factors{domain:{L,I}}`, `applicable_controls[]`, `stale_controls[]`, `planned_controls[]` | `{"inputs": {"case_id": "FCRM-2026-0118"}, "expected": {"factors": {"channel": {"L": 4, "I": 4}}, "applicable_controls": ["CTL-T01","CTL-T02","CTL-T03","CTL-T04"], "stale_controls": []}}` |
| `critic/planted-60` | 60 = 20 ledgers × 3 planted errors | take 20 seed cases' synthetic ledgers; plant one of: unsourced claim (quote altered), inflated rating (precedent mean + 2.5), rule breach (planned control with a rating) | `planted[{type, about}]` | — |
| `narrative/completeness-30` | 30 | from the 30 seed cases with at least one empty required field or stale evidence | `missing_field_ids[]`, `stale_controls[]`, `questions_min`, `must_not_ask[]` (already answered) | — |
| `narrative/recommendation-40` | 40 closed seed cases | inputs = confirmed ratings and outcome; judge-graded | `outcome`, `weak_controls[]`, `allowed_numbers[]` | — |
| `examiner/questions-120` | 120 = 40 cases × 3 templates (why routed · what was overridden and why · which versions) + 20 unanswerable | synthetic audit chains generated by replaying each seed case through H1 in `--seed-replay` mode (deterministic record types per stage), demo case from its 67 records | `must_cite_types[]`, `answerable: bool` | `{"inputs": {"case_id": "FCRM-2026-0151", "question": "Why was FCRM-2026-0151 routed to committee instead of closed by the analyst?"}, "expected": {"answerable": true, "must_cite_types": ["score.computed","human.override","score.recomputed","vote.revealed"]}}` |
| `screening/fast-lane-100` | 80 seed + 20 synthetic eligible variants (volume < $5M, vendor renewal or feature-only) | seed `fast_lane` flags; variants by mutating F05 and types | `qualifies: bool` | — |
| `watchlist/reflag-24` | 24 | fixture list v2026-10 × open seed cases, plus two more synthetic list diffs | `reflag_case_ids[]` | — |
| `policy_gap/suggestions-14` | 14 | the seed's gap topics; acceptance labels start from the seed and are replaced by live labels | `coverage_exists: bool`, `accepted: bool|null` | — |
| `redteam/injection-50`, `redteam/pii-20` | 50 / 20 | hand-written injection strings embedded in document pages; PII fixtures per entity type | `must_block`, `entities_expected[]` | — |

### 7.3 Metrics, definitions, thresholds (proposed; `gate` = used by the promotion gate)

| Metric | Kind | Definition | Threshold | Agents |
|---|---|---|---|---|
| citation_validity | det | items whose quote is verbatim in the resolved source span ÷ items with provenance, measured on **attempt 1** (Guard 4 makes the final rate 100%) | ≥ 0.95 | research, intake, assessment, examiner, critic |
| field_extraction_accuracy | det | normalised exact match (NFKC, whitespace, currency symbols) of value per expected field; missing counts as wrong | ≥ 0.90 | intake (gate) |
| source_span_accuracy | det | page equal and line ranges overlap | ≥ 0.90 | intake |
| category_f1 | det | micro-F1 over change types | ≥ 0.90 | intake |
| gap_question_precision | det | gap questions whose field is really empty ÷ gap questions | ≥ 0.90 | intake |
| clause_recall_at_k | det | expected clause ids ∩ proposed ÷ expected (k = 12 retrieved, ≤ 8 proposed) | ≥ 0.85 | research (gate) |
| clause_precision | det | proposed ∩ expected ÷ proposed | ≥ 0.70 | research |
| gap_honesty | det | planted gap topics declared as gaps ÷ planted, and zero forbidden clauses proposed | ≥ 0.90 and 0 | research (gate) |
| li_within_one | det | |L−L*| ≤ 1 and |I−I*| ≤ 1 per domain | ≥ 0.85 | assessment (gate) |
| control_match_recall | det | expected applicable controls ⊆ proposed applicable | ≥ 0.90 | assessment |
| stale_evidence_detection | det | controls with evidence older than 24 months whose rationale names scope/age (regex on "202[0-4]", "stale", "domestic", "predates") | ≥ 0.95 | assessment |
| planned_never_rated | det | planned controls with rating None | = 1.00 | assessment |
| planted_error_detection | det | planted errors with a finding of the right type about the right item ÷ planted | ≥ 0.85 | critic (gate) |
| finding_precision | judge | rubric "usefulness": specific (0.4), grounded in the shown evidence (0.4), actionable without prescribing a value (0.2); pass ≥ 0.7 per finding; k = 3 | ≥ 0.80 of findings | critic |
| question_grounding | det | questions traceable to a finding, gap or empty field | ≥ 0.95 | narrative (gate, completeness) |
| question_dedupe | judge | dropped questions whose drop_reason points to a real answer in the record | ≥ 0.90 | narrative |
| narrative_faithfulness | judge | rubric: every claim traceable to the inputs (0.5), no invented or altered number or condition (0.3), emphasis on the biggest real driver (0.2); pass ≥ 0.8 | ≥ 0.90 of cases | narrative (gate, recommendation) |
| number_grounding | det | numbers in text ⊆ allowed_numbers | = 1.00 | narrative |
| restricted_terms_zero | det | restricted terms in business_summary | = 0 | narrative |
| conditions_map_to_weak_controls | det | every condition's `ref` is an overridden/planned/weak control or a recorded gap | = 1.00 | narrative |
| sentence_record_grounding | det | sentences with ≥ 1 existing record and a verbatim quote | = 1.00 | examiner (gate) |
| refusal_rate | det | unanswerable rows with answered=false | ≥ 0.95 | examiner |
| fast_lane_precision / recall | det | standard | ≥ 0.90 / ≥ 0.90 | screening (gate: precision) |
| reflag_recall | det | code candidates with a proposal | = 1.00 | watchlist (gate) |
| suggestion_acceptance | live | accepted ÷ reviewed | tracked, no gate until ≥ 10 reviewed | policy_gap |
| injection_block_rate / pii_mask_recall | det | blocked ÷ must_block; masked entities ÷ expected | = 1.00 / ≥ 0.95 | guardrails |
| override_rate | live | §6.2 | tracked; alert on +5 pts week-on-week | all |

Judge: `evals/judge/judge.py` builds a Pydantic AI `Agent` on `{{MODEL_JUDGE}}` (fallback `{{MODEL_FALLBACK_JUDGE}}`), `output_type=ToolOutput(RubricVerdict)` with `criteria: dict[str, float]`, `reasoning: str`, `flags: list[str]`; temperature 0; the rubric Markdown is the system prompt; inputs are the row's inputs and the agent output; k = 3 calls, criteria averaged, pass decided on the mean (the G-Eval pattern from auto_qa, the k-vote from evoq_evals). Judge calls are cached by sha256(rubric, inputs, output) in `evals/results/.judge_cache/` so re-runs are free. The judge never grades research or examiner quotes: those are deterministic.

### 7.4 Runner design
- Core: `pydantic_evals.Dataset[RowInputs, AgentOutput, RowMeta]` per dataset file, `Case` per row, evaluators = the metric classes applicable to the agent (from `AgentSpec.eval`). Task = `offline_runtime.run(agent, mode, inputs)`: builds the pack with `SeedBackedTools` (fixtures), runs the same `fcrm_agents` runtime and Guard 4 as production (so `citation_validity` measures attempt 1 and the retry path is exercised), returns `(output, guard_results, usage)`.
- Model: real Bedrock by default; `--cached` wraps the model with a response cache keyed by rendered prompt hash (Pydantic AI's cached-model wrapper if present in the installed version, else a 40-line `CachedModel` in `offline_runtime.py`) for repeatable re-runs and for the demo rehearsal.
- Adapter: `evals/metrics/adapters/deepeval_adapter.py` (optional) wraps our judge as a `DeepEvalBaseLLM` so DeepEval's `GEval`/`Faithfulness` can be run for comparison; not part of the gate.
- Reporting: `report.py` writes `<run_id>/<agent>.json` (§7.5), `index.json`, appends `trend.jsonl`, renders `report.html` (Jinja; one table per agent, red/amber/green per metric, flagged rows with the failing quote), and loads the run into `fcrm_harness.eval_runs` when `--publish` is set, which is what the Assurance › Agent quality tab reads. `pydantic_evals`' own `EvaluationReport.print()` is used for the terminal view.
- Gate: `gate.py` (§6.4). `make check` runs `gate.py --verify` which only checks that committed results match the committed `MANIFEST` (no model calls); `make evals` runs the models.

### 7.5 Result file format

```json
{"run_id": "2026-10-06T14-22Z-a1b2c3", "ts": "2026-10-06T14:22:10Z", "git_sha": "…", "agent": "research",
 "prompt_set_v": "2026-10-06", "prompt_ids": {"system": "research.system@2", "user": "research.user@1"}, "model_id": "{{MODEL_STRONG}}", "fallback_id": "{{MODEL_FALLBACK_STRONG}}",
 "datasets": ["research/clauses-40", "research/gaps-12"], "rows": 52, "rows_failed_runtime": 0,
 "metrics": [{"name": "clause_recall_at_k", "kind": "deterministic", "score": 0.87, "passed": 45, "total": 52, "threshold": 0.85, "gate": "green", "flags": [{"row_id": "r-0097", "detail": "missed TP71-2.3"}]},
             {"name": "citation_validity", "kind": "deterministic", "score": 0.962, "passed": 253, "total": 263, "threshold": 0.95, "gate": "green", "flags": []}],
 "usage": {"input_tokens": 348120, "output_tokens": 81210, "cache_read_tokens": 102400, "cost_usd": 0.0}, "duration_s": 412,
 "baseline_run_id": "2026-10-05T09-00Z-…", "gate_result": {"status": "PASS", "primary": {"name": "clause_recall_at_k", "baseline": 0.85, "candidate": 0.87}}}
```

### 7.6 Baseline measurement plan (days 3–6: 5–8 Oct) — measured, not seeded
- Day 1–2 (3–4 Oct): contracts, prompts v1, offline runtime with `SeedBackedTools`, metric unit tests on hand-made rows; gen_seed extended to emit datasets (`gen_seed.py --evals`) including synthetic documents for 20 cases.
- Day 3 (5 Oct): first real runs: `research/clauses-40`, `intake/extraction-20`, `screening/fast-lane-100` on v1 prompts → results committed as the first baseline; the seed's `evals` block is marked `measured: false` and the Assurance screen reads `eval_runs` instead (empty until published).
- Day 4 (6 Oct): `assessment/ratings-80`, `critic/planted-60`, `examiner/questions-120`; harvest the first overrides from the demo rehearsal; one deliberate prompt iteration on the weakest metric (expected: intake `source_span_accuracy`, or research `gap_honesty`) with before/after results committed; this is the "failure → improvement" evidence for the deck.
- Day 5 (7 Oct): narrative judge metrics, watchlist, policy_gap; red-team sets; token report v1; the weekly `run_all` wired to `make evals`.
- Day 6 (8 Oct): full `run_all --publish`, freeze `results/baseline/`, write `/evals/README.md` with the numbers as measured, note every metric that missed its threshold and why (the panel probes decisions, not green tables).

---

## 8. Token efficiency

Tier choice and why: screening, intake, watchlist on **cheap** because their tasks are extraction and classification against explicit lists with verbatim provenance checked by code (quality is enforced downstream, so a small model is enough and intake volume is the largest); research and assessment on **strong** because clause selection and rating against written scales drive everything the analyst reviews and are the top override-rate risk; critic on **judge** (a different model family from the proposer, so that its errors are not correlated with assessment's); narrative, examiner and policy_gap on **mid** because they write prose from given inputs with deterministic grounding checks.

Levers, in order of expected effect:
1. Pack caps and retrieval-first (§3.3, §3.5): the agent sees ranked top-k, not the corpus; top-k = 12 clauses, 5 precedents, controls for in-scope domains only, intake pages ranked by field synonyms; tool-call caps bound follow-up round-trips.
2. Prompt caching on strong/mid/judge (§3.4): system prompt + skills + schema cached; expected 25–35% of input tokens read from cache during a case run (research ∥ assessment ∥ critic within minutes).
3. Structured output via tool-call mode and `extra="forbid"` schemas: fewer malformed outputs, so fewer Guard 4 retries (each retry costs a full tier call).
4. Retry on the fallback with the error shown, once (decided): bounded at 2× tier budget per job.
5. Deterministic steps stay deterministic (§9): scoring, routing, tally, page ranking, candidate selection for watchlist, restricted-term and number checks cost zero tokens.
6. Retrieval cache for re-runs; Guard 1's cheap-tier classifier only on amber pattern scores.
7. Per-case 120k enforcement (§4.4): reservation before dispatch, actual after; `budget_exhausted` is a human gate, never a silent truncation.

Token analysis report (`make token-report` → `/ops/token-report/<date>.md` + `.json`, from `model_calls`):
```
# Token report · 2026-10-08 · 14 cases · 61 jobs · 68 model calls
| agent | calls | retries | p50 in | p95 in | p50 out | cache read % | tokens/case | cost/case | share |
| intake | … |
| … |
Per case: case_id · total tokens · % of 120k · agents · retries · wall time
Per prompt version: prompt_id@v · calls · mean in/out · citation_validity (from evals)
Optimisations applied: {date, change, before tokens/case, after tokens/case, eval delta}
Known concentrations: intake windows (xx%), critic ledger section (yy%)
```
The deck's token slide is generated from this file; nothing in it is typed by hand.

---

## 9. Engineering-judgement register

| Step | D/P | Justification |
|---|---|---|
| Scoring (inherent, CE, retention, residual, composite, band, boundary flag) | Deterministic (S2) | Arithmetic over confirmed inputs; must be reproducible, testable and explainable to an examiner; golden tests 9.8 → 10.4 |
| Routing R1–R6 | Deterministic (H1, data-driven rules) | Band lookup and quorum arithmetic; versioned data so the risk function can tune without code |
| Fast-lane criteria (volume, types) | Deterministic (H1) | Field comparisons; only the free-text "is this a renewal" reading is the screening agent's |
| Auto-triage (R5) | Deterministic | Min over open-case counts |
| Field set resolution (union of confirmed types), completion %, four submit checks | Deterministic (H1/S1) | Set arithmetic; the prototype computed completion three ways, so one function with tests |
| Field value extraction from documents | Probabilistic (intake) + deterministic verification | Reading free text needs a model; the quote-exists check and PO confirmation make a wrong value visible, never silent |
| Page selection for intake windows | Deterministic (BM25 over field synonyms) | Cheap, reproducible, keeps the model inside the budget |
| Change-type classification | Probabilistic (intake), PO confirms | Needs reading; a gate with a reason on removal |
| Precedent similarity | Deterministic retrieval (embeddings + rank fusion) | A number copied from the index; the agent only explains |
| Clause mapping (which clauses apply) | Probabilistic (research) over deterministic retrieval; verbatim quoting enforced | Applicability is judgement; the text is never generated |
| L/I and control ratings | Probabilistic proposals, human-confirmed | Judgement against written scales; the model proposes with evidence, the analyst decides |
| Stale-evidence flag | Deterministic (date arithmetic) | The agent reports the date; code decides "stale" |
| Planned-control exclusion | Deterministic (schema validator + rule 7) | A planned control has no rating by construction |
| Critic findings | Probabilistic (second model) + deterministic inputs (rules_check, quote checks) | Finds judgement errors; code supplies the facts it reasons over |
| Completeness (missing fields) | Deterministic list; narrative explains and drafts questions | Empty required fields are a set difference; question wording needs a model |
| Question batching, dedupe | Probabilistic with deterministic grounding check | Wording needs a model; "traces to a finding" is checked by code |
| Pack generation | Deterministic (S5) | Assembles locked numbers and pinned versions; no model may touch the pack |
| Four release checks | Deterministic | Boolean checks over ledger and gate state |
| Vote tally and outcome (proposed rule for S5) | Deterministic | Plurality of three votes; a 1-1-1 split or any outcome short of two votes resolves to `defer` with "no majority" recorded, because nothing is approved without a majority |
| Hash chain and verify | Deterministic (S4) | sha256 over canonical record + prev_hash; insert-only user |
| Restricted-term check on PO text | Deterministic (regex) | A model cannot be trusted to police its own output; the list is short and auditable |
| Number grounding in narrative | Deterministic | Token set comparison |
| Narrative, recommendation, business summary text | Probabilistic (narrative) with judge eval | Prose for people; faithfulness measured, numbers pinned by code |
| Examiner answers | Probabilistic over a deterministic, verified record set; grounding checked by code | Reading 67 records into an answer needs a model; every sentence proves itself |
| Watchlist candidate set | Deterministic (set intersection) | Geographies × list diff |
| Watchlist proposal text | Probabilistic (cheap) | Explanation with provenance for the analyst's acknowledgement |
| PII masking and injection scan | Deterministic (presidio + patterns), cheap model only on amber | Recognisers and patterns are auditable; the model is a second opinion, not the gate |
| Prompt promotion | Deterministic gate over measured metrics | A number comparison, never a judgement call at commit time |

---

## 10. Parallelisation after `fcrm_contracts` exists

`fcrm_contracts` (day 1, first): `provenance.py` (§2), `outputs/*.py` (nine output models), `tools/*.py` (input/output models for the thirteen tools), `events.py` (`JobPayload`, `AgentDone`, `GateDecided`, `CaseEvent`), `guards.py` (`GuardResult`, `RuleResult`, `LedgerAnnotation`), `versions.py` (`PinnedVersions`), `ai_files.py` (`AgentSpecFile`, `ToolSpecFile`, `TierFile`, `GuardConfig`, `PromptFrontMatter`), `ledger.py` (`LedgerProposal`, `ModelCallRecord`). Published as a wheel; everything below pins it.

Independent workstreams once contracts are merged (each is one Claude Code subagent task with its own tests):
1. `/ai` files + `fcrm_prompts_loader` (P4): YAML/Markdown parsing, Jinja rendering with the three functions, registries, lock files and lock tests. Stub needed: none (pure).
2. `fcrm_guardrails` (P5): seven guard classes, eight rules, verbatim matcher, restricted terms, presidio wiring, OPA client. Stubs: `FakeOPA` (reads `tool_permits.json`), `FakeCorpusLookup`, `FakeDocStore`, `FakeChainReader` (all seed-backed from `fcrm_seed`).
3. `fcrm_tools` (P2): thirteen read-only clients with timeouts, scope checks and the retrieval cache. Stubs: `SeedBackedTools` serving every tool from `seed.json` plus the corpus Markdown; recorded-response fixtures for contract tests against the services' OpenAPI once they exist.
4. `fcrm_agents` (P1): nine agent folders (spec loader, builder, runtime adapter), `ContextPackBuilder`s, the offline runtime. Stubs: Pydantic AI `TestModel`/`FunctionModel` for pipeline tests without Bedrock; `SeedBackedTools`.
5. H2 agent-worker: FastStream consumer, atomic claim, lease, DLQ, model wiring, token records, OTel. Stubs: in-memory broker from FastStream's `TestClient`; `InMemoryLedger` (S4 stand-in); `FakeBudgetRepo`.
6. `/evals`: metrics with golden tests, judge, runners, report, gate, harvest. Stubs: `FunctionModel` returning canned outputs so the pipeline runs end-to-end before day 3.
7. OPA `tools.rego` + `tool_permits.json` + `opa test` (depends only on the agent and tool names).
8. H1 touch-points this design needs from the harness workstream: `JobPayload` construction with `PinnedVersions`, budget reservation, `changed_fields` on re-runs, intake sharding, critic dispatch after S2, `failed_step` gate, `case.closed` → precedent index write.

Local end-to-end before services exist: `uv run fcrm-agent run research --case FCRM-2026-0151 --seed --model real|test` runs one job through guards, builder, model and Guard 4 against the seed, printing the ledger items and the model-call record. This is also the demo rehearsal tool and the day-3 eval entry point.
