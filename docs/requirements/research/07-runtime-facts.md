# G · Verified runtime facts (3 Oct 2026)

## AWS account (region us-east-1, default profile)
Verified via boto3 1.39.11 (the `aws` CLI at /usr/local/bin/aws is an x86-64 Linux ELF on an arm64 Mac: "exec format error"; reinstall or use boto3/uvx awscli).

Bedrock inference profiles ACTIVE (use these as model IDs; `us.` = US cross-region, `global.` also available):
- `us.anthropic.claude-opus-5-5`
- `us.anthropic.claude-sonnet-5-5`
- `us.anthropic.claude-haiku-4-5-20251001-v1:0`
- `us.anthropic.claude-fable-5-1` (most capable; not needed for tiers)
- `us.amazon.nova-pro-v1:0`, `us.amazon.nova-lite-v1:0`, `us.amazon.nova-premier-v1:0`, `us.amazon.nova-2-lite-v1:0`
- Embeddings ON_DEMAND: `amazon.titan-embed-text-v2:0` (1024-d)

S3 Vectors reachable; existing vector buckets: `evoq-s3-vectorbucket-1`, `evoq-test-bucktet` (create a dedicated `trustops-vectors` bucket; do not reuse).

## Model tiers (decision, replaces the docs' anthropic:/openai: aliases; AWS everywhere)
| Tier | Primary | Fallbacks (FallbackModel order) | Used by |
|---|---|---|---|
| cheap | us.anthropic.claude-haiku-4-5-20251001-v1:0 | us.amazon.nova-lite-v1:0 | screening, intake, watchlist |
| mid | us.anthropic.claude-sonnet-5-5 | us.anthropic.claude-haiku-4-5-20251001-v1:0 | narrative, examiner, policy_gap |
| strong | us.anthropic.claude-sonnet-5-5 | us.anthropic.claude-opus-5-5, us.amazon.nova-pro-v1:0 | research, assessment |
| judge | us.anthropic.claude-opus-5-5 | us.anthropic.claude-sonnet-5-5 | critic, eval judges |
Rationale: critic/judge runs on a different model than the assessor it checks. Pricing on Bedrock is partner-set; record per-call cost from a `ai/models.yaml` price table and reconcile against the AWS bill.

## Pydantic AI (latest 2.53.0, released 2 Oct 2026; Python >=3.10; pydantic-evals is a separate package)
- Install: `pydantic-ai-slim[bedrock]`; `from pydantic_ai.models.bedrock import BedrockConverseModel`; `from pydantic_ai.providers.bedrock import BedrockProvider` (`BedrockProvider(bedrock_client=boto3.client('bedrock-runtime', region_name=...))` or `region_name=`).
- Model string form: `"bedrock:us.anthropic.claude-sonnet-5-5"`.
- Bedrock settings (`BedrockModelSettings`): `bedrock_cache_instructions`, `bedrock_cache_tool_definitions`, `bedrock_cache_messages` (TTL '5m'/'1h'), manual `CachePoint()`; `bedrock_guardrail_config`; `bedrock_performance_configuration`; `bedrock_service_tier`. Usage exposes `cache_write_tokens` / `cache_read_tokens`.
- `from pydantic_ai.models.fallback import FallbackModel`; `FallbackModel(primary, secondary, ...)`; triggers on `ModelAPIError` (4xx/5xx) by default, customisable via `fallback_on`.
- `from pydantic_ai import Agent, ModelSettings, UsageLimits, UsageLimitExceeded, ModelRetry, RunContext`.
  - `UsageLimits(request_limit=, output_tokens_limit=, tool_calls_limit=, cost_limit=Decimal, per_request_input_tokens_limit=, count_tokens_before_request=)` passed to `run(..., usage_limits=)`.
  - `ModelSettings(max_tokens=, temperature=, timeout=)`; precedence model < agent < run.
  - `Agent(model, output_type=Model | [A, B], instructions=, deps_type=, retries={'tools': n}, tools=[...], instrument=True)`; `@agent.tool` (RunContext) / `@agent.tool_plain`; `@agent.output_validator` may `raise ModelRetry(...)`.
  - Output modes: ToolOutput (default, use for Bedrock/Anthropic), NativeOutput, PromptedOutput (fallback).
  - Observability: `Agent.instrument_all(InstrumentationSettings(tracer_provider=, meter_provider=, include_content=, version=5))` with a plain OTLP exporter; spans carry `gen_ai.request.model`, `gen_ai.usage.input_tokens/output_tokens`, `gen_ai.aggregated_usage.*`, `operation.cost`.

## Local toolchain
- Present: python3.13 (3.13.5) with boto3 1.39.11 and pydantic 2.11.7; Docker 28.3.2; Compose v2.38.2; cached images mongo:8, rabbitmq:4.0.4-management, redis:7, openpolicyagent/opa:latest, nginx:alpine.
- Missing: uv, node, npm, brew, nvm/fnm/volta, pipx. Plan: install uv via the official script (user-space ~/.local/bin) and Node 22 LTS arm64 from the official tarball into ~/.local (no sudo); every Node step also runs in containers (node:22 for web build, mcr.microsoft.com/playwright for E2E) so the repo does not depend on host Node.
- Disk: 47 GB free.
