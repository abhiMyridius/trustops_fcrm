# ai/ — the AI layer as files

Everything an agent is, sees and may do lives here as data: specs, prompts, skills, tool definitions, model tiers, guard configuration and the OPA permit table. No Python package reads a prompt or a model id from anywhere else. The runtime packages that load these files are built in other lanes: `fcrm_prompts_loader` (P4) and `fcrm_agents` (P1), run inside the agent-worker H2. Design reference: `docs/architecture/design/B-ai-layer.md` §1–§4.

## What each folder holds

| Path | What it is | Read by |
|---|---|---|
| `agents/<name>.agent.yaml` | One spec per agent (nine): tier, budget, tools the model may call, `pack_tools` the context builder may fetch, skills, output schema, prompt ids, guards, the gate it feeds, eval datasets | `fcrm_prompts_loader` → `fcrm_agents` registry; H1 for gates |
| `tools/<name>.tool.yaml` | One spec per read-only tool (thirteen): description the model sees, backing service and route, permit key, inline input/output field lists, cache and scope rules | `fcrm_tools` clients; the loader renders `description` into the model's tool list |
| `skills/<name>.skill.md` | Five instruction packs, plain Markdown with front-matter | included into system prompts by `{{ skill("id") }}` |
| `prompts/MANIFEST.yaml` | The prompt set version (= the case pin `prompts_v`) and the current version of each prompt id | the loader; every case pins `set_version` |
| `prompts/<agent>/<id>.v<N>.md` | System and user prompt templates (Markdown + YAML front-matter, Jinja2) | the loader's `render(prompt_id, variables)` |
| `prompts/CHANGELOG.md` | One row per prompt version with the reason and the eval run | people |
| `models/tiers.yaml` | Tiers (cheap, mid, strong, judge), primary and fallback model ids, budgets, request constraints, price table, embeddings | the runtime when it builds `FallbackModel`s; the token report |
| `guards/guards.yaml` | The seven guards in order, where each runs, what it applies to, failure behaviour | `fcrm_guardrails.GuardChain` |
| `guards/thresholds.yaml` | Numbers: quote length, confidence floor, stale-evidence months, deviation flag, injection scores, pack section caps | `fcrm_guardrails`; prompts via the `thresholds` namespace |
| `guards/injection_patterns.txt` | Guard 1 pattern list, weighted, one regex per line | `fcrm_guardrails.input` |
| `guards/pii_entities.yaml` | Presidio entities, custom recognisers, the signature-block regex, the allow-list of seeded names | `fcrm_guardrails.input` |
| `guards/restricted_terms.txt` | Terms that may never reach the product owner | Guard 4 on the business summary; S1's PO response middleware |
| `policies/tools.rego`, `tool_permits.json`, `tools_test.rego` | OPA agent→tool permits, source copy (copied into `ops/opa` at build) | OPA; the Python allow-list fallback in `fcrm_tools` |
| `registry.lock/*.lock` | `name<TAB>version<TAB>sha256` per agent, tool, skill and prompt file | lock tests; H2 at start-up |
| `schemas/` | Generated JSON Schema exports (`make schemas`), never hand-written | the panel; web typegen |
| `guidance/` | Build-time guidance for Claude Code: standards, commit convention, brief template, worktree protocol, review checklist, hook scripts | Claude Code and its subagents |
| `_tools/` | Bootstrap scripts: `compute_locks.py` (writes the locks) and `validate_ai.py` (consistency checks) | people, `make locks`, the Stop hook |
| `evals-index.md` | Which eval datasets guard which prompt | people |

## How the runtime loads it

1. At process start H2 mounts `/ai` read-only at `/app/ai`. `fcrm_prompts_loader` parses every YAML and front-matter block and validates it against the contract models (`AgentSpecFile`, `ToolSpecFile`, `TierFile`, `GuardConfig`, `PromptFrontMatter` in `fcrm_contracts`).
2. It builds four registries (agents, tools, skills, prompts) and compares each file's sha256 with `registry.lock/`. A mismatch stops H2 from starting, and `make check` fails on it.
3. For each agent it composes the output type. `output_schema.model` is a single `fcrm_contracts` class. `output_schema.envelope` is an envelope model built from the listed `fields`:
   - `status: contract`: the class exists in `fcrm_contracts`;
   - `status: contract_change_required`: the class exists but needs the change written under `contract_changes_required`;
   - `status: pending_contract`: the class does not exist yet;
   - `status: primitive`: a plain type (`bool`, `int`, `list[str]`, an enum).

   The loader refuses to start an agent whose envelope has a `pending_contract` part, so a prompt never asks for a field the schema would reject. When `fcrm_contracts` gains a named envelope class (for example `ResearchOutput`) in `envelope_module`, the loader uses that class instead.
4. For each job, H1 sends a small `JobPayload` with the pinned versions. H2's `ContextPackBuilder` fetches through the agent's `pack_tools`, renders the user template with `section()` caps from `guards/thresholds.yaml`, renders the system prompt with `versions`, `thresholds`, `skill()`, `schema()` and `config()`, places cache points, and snapshots the pack.
5. The model gets the rendered system text, the user text and the agent's `tools`. Each tool call goes through Guard 2 (OPA `data.tools.allow`, with `caller: model`), and so does each builder fetch (`caller: builder`).

## Changing a prompt

A committed prompt file never changes. To change one:

1. Copy `prompts/<agent>/<id>.v<N>.md` to `<id>.v<N+1>.md`. Edit the copy, bump `version`, and add a changelog entry with `eval_run: pending`.
2. Run `make evals AGENT=<agent> CANDIDATE=<id>@<N+1>`. Promotion needs the primary metric at or above the baseline and every gated metric at or above its threshold (design B §6.4).
3. On PASS, set `eval_run` to the run id, move `current:` for the id in `MANIFEST.yaml`, bump `set_version` (ISO date, `.n` for a second bump that day), append a `history` row and a `CHANGELOG.md` row, then run `make locks` (until it exists: `python3 ai/_tools/compute_locks.py`).
4. Commit the prompt, the manifest, the locks and the eval results together: `ai(<agent>): ... [ADR-nn]` with `Stage:` and `Gate:` trailers.

Running cases keep the set they pinned. New cases get the new set.

## Model ids live in one file

Model ids appear only in `models/tiers.yaml`. Every other file names a tier. A lock test elsewhere (`tests/registry/test_locks.py::test_no_model_ids_outside_tiers`, owned by the loader lane) greps the repository for model-id shapes and fails on any hit outside that file. `ai/_tools/validate_ai.py` runs the same check over `ai/`, `src/backend`, `src/frontend/src`, `evals` and `ops` (the few files that may name an id, each with its reason, are `MODEL_ID_ALLOWED` there), and `make check` runs it through `make ai-check`. To change a model: re-verify the id against the Bedrock account, edit `tiers.yaml`, add a decision-log row, and re-run the evals for every agent on that tier.

## Checking this folder

```bash
python3 ai/_tools/validate_ai.py            # needs PyYAML + Jinja2 (+ pydantic for the contract import check)
OPA_BIN=/path/to/opa python3 ai/_tools/validate_ai.py   # also runs opa check --strict, opa test, two decisions
python3 ai/_tools/compute_locks.py --check  # locks match the files
opa test ai/policies -v
```
