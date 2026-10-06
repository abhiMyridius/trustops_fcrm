# The AWS production path

**This is a design, not a deployment.** Nothing in this folder has been deployed. There is no Terraform, CDK or CloudFormation in the repository. What exists and runs is the eight-container compose stack on one machine, calling real Amazon Bedrock. This page says how the same code would run on AWS, why each choice, which environment variables change, and what is known not to work yet.

The contract it is tied to: `.env.example` is the full list of variables, and `ops/scripts/check_env_contract.py` (`make compose-check`) keeps it equal to the compose files. A deployment replaces compose; it must supply the same variables.

## Target shape

```
Route 53 / ACM  ->  ALB (TLS, WAF, idle timeout 120 s)
                      |
                      v
             ECS Fargate service "gateway"  (nginx, ops/nginx/default.conf)
                 |                |
                 v                v
     service "web" (nginx,     service "api"  (X1 + S1-S5, :8000)  + OPA sidecar
      static build)                |
                                   |  HTTP (Service Connect name "api")
                                   v
                          service "harness" (H1-H4, health :9100) + OPA sidecar
                                   |
   +-------------+-----------------+------------------+--------------------+
   v             v                 v                  v                    v
 MongoDB      Amazon MQ        S3 buckets        Amazon Bedrock       ADOT collector
 (Atlas)     (RabbitMQ)        (5 buckets)       (VPC endpoint)       -> X-Ray, CloudWatch
```

| Compose container | AWS | Why |
|---|---|---|
| `gateway` (nginx) | ECS Fargate behind an ALB | The gateway is not only a router. It hides every `/internal/` path (404), maps the SSE `?access_token=` to a header, sets per-route timeouts (75 s for the examiner question, 300 s for uploads, 1 h for streams), limits login to 10 per minute and body size to 60 MB on documents. An ALB alone would not map the SSE token query parameter to a header or hide the internal paths, so the gateway stays; the ALB adds TLS and WAF in front of it |
| `web` (nginx, static build) | ECS Fargate (or S3 and CloudFront) | The image is a static build; any static host works. Keeping it behind the gateway keeps one origin and the same cookie and CORS behaviour as locally |
| `api` | ECS Fargate, 1 task to start | One process hosts identity and S1-S5 (ADR-26). Health: `GET /readyz` on 8000. See the scaling note |
| `harness` | ECS Fargate, 1 task to start | H1-H4 on one broker. Health: `GET /readyz` on 9100. Graceful stop needs a `stopTimeout` of 70 s or more (compose sets 70) |
| `opa` | sidecar in the `api` and `harness` tasks | Every request and every agent tool call asks OPA (default deny, fail closed). A sidecar makes `OPA_URL` `http://localhost:8181`, removes a network hop and a single point of failure. Policy and data (`ops/opa/policy`, `ops/opa/data`) are baked into the sidecar image instead of bind-mounted |
| `mongo` | MongoDB Atlas on AWS | See the next section |
| `rabbitmq` | Amazon MQ for RabbitMQ | Managed broker, same protocol; the code does not change |
| `minio` | S3 | The code already speaks the S3 API |
| `redis` | ElastiCache, only if needed | Nothing uses it today (see "Known blockers") |
| `otel-collector`, Jaeger, Prometheus, Grafana | ADOT collector sidecar to X-Ray, CloudWatch and CloudWatch Logs | The services speak OTLP; only the exporters change (ADR-09) |

## MongoDB

What the code needs from the database (read from the code, not assumed):

- A **replica set** and **multi-document transactions** with `snapshot` read concern and `majority` write concern. Every state change commits its audit-bound outbox event in one transaction (`fcrm_common/mongo.py`, `outbox.py`, `events.py`; ADR-28).
- `$jsonSchema` collection validators (`ops/mongo/init/03-schemas.js`), TTL indexes (consumed messages, published outbox rows, idempotency keys, refresh tokens), the `$unionWith` aggregation stage (S4 override analytics).
- **Custom roles** with per-collection privileges: the insert-only audit role `fcrmAuditInsertOnly` (`find` and `insert` only) and the read-only evals role (`02-users.js`). This is what makes the audit chain tamper-resistant at the database as well as tamper-evident.
- Change streams are **not** used (a grep for `.watch(` finds nothing); the outbox relay polls under a lease.

| Option | Fit | Caveat |
|---|---|---|
| **MongoDB Atlas on AWS** (recommended) | It is MongoDB: transactions, validators, TTL, `$unionWith` and custom roles are the product's own features | A paid service outside the AWS account; private connectivity through PrivateLink; the Mongo init scripts need an Atlas-aware run (skip `01-replset.js`, which calls `rs.initiate`) |
| Self-managed MongoDB 8 on EC2 (3 nodes) | Identical to local | You run backups, upgrades and failover |
| **Amazon DocumentDB** | AWS-native | **Not verified.** I could not check, offline, DocumentDB's current compatibility with: snapshot-isolation transactions with majority write concern, `$jsonSchema` validators, custom roles scoped to collections and actions, `$unionWith`, TTL indexes and text search. The audit design depends on the custom role. Treat DocumentDB as unproven until each is tested |

Run the init once per environment as a one-off ECS task with an admin URI: users, the insert-only role, validators and indexes (`ops/mongo/init/02-users.js`, `03-schemas.js`, `04-indexes.js`). Three URIs go to the application: `MONGODB_URI` (the `fcrm_app` user, every service and worker), `MONGODB_URI_AUDIT_WRITER` (S4 only) and `MONGODB_URI_EVALS_READER` (S4 only). Use `mongodb+srv://` URIs with `retryWrites` and a `replicaSet` the provider gives you.

## Messaging, objects and vectors

- **Amazon MQ for RabbitMQ.** The topology (`ops/rabbitmq/definitions.json`) needs quorum queues, at-least-once dead-lettering, a `delivery-limit` policy and the shovel plugin (`make dlq-replay`). Import the definitions through the management API; the compose entrypoint that adds a user does not apply. **Not verified:** which RabbitMQ versions Amazon MQ offers today and whether it supports every one of those settings. Pick a cluster deployment for high availability; the single-instance size is the cheap non-HA option. `RABBITMQ_URL` becomes the `amqps://` endpoint.
- **S3 instead of MinIO.** `fcrm_common.objectstore` builds a boto3 S3 client and uses the SDK credential chain when `S3_ENDPOINT_URL` and the static keys are unset (`build_client`). Create the five buckets (`uploads`, `canonical`, `packs`, `vectors`, `jobs`). Enable versioning on `packs`, as `ops/minio/init.sh` does; consider S3 Object Lock there, so a frozen pack cannot be overwritten (not designed in the code). Block public access; the code reaches objects through presigned URLs. Do not put `S3_ENDPOINT_URL` in the task definition; compose's `${S3_ENDPOINT_URL:-http://minio:9000}` default is a compose behaviour that ECS does not have.
- **Vectors.** The shipped default is `VECTOR_BACKEND=memory` with `EMBEDDING_PROVIDER=bedrock`: each `api` task embeds the 197 corpus clauses on Bedrock when it starts (about 85 seconds to become healthy). The S3 Vectors backend exists in the code (`evoq_context/vector/s3_vectors.py`, `VECTOR_BACKEND=s3vectors`), **but it is not in the shipped image**: it needs `aioboto3`, an optional extra that cannot resolve in the same environment as the agents (aiobotocore caps `boto3` below the version `pydantic-ai-slim[bedrock]` needs; `src/backend/pyproject.toml` declares the conflict). To use S3 Vectors, split H4 into its own image built with the extra, or remove the pin conflict. Until then the AWS path runs the memory backend and accepts the start-up embedding.

## Bedrock, identity and secrets

- **Bedrock** through an interface VPC endpoint for `bedrock-runtime`, in the region of `AWS_DEFAULT_REGION` (`us-east-1`). The agents use the Converse API through Pydantic AI's `BedrockConverseModel`; the models are the US cross-region inference profiles named in `ai/models/tiers.yaml` (labels: Haiku 4.5, Sonnet 5.5, Opus 5.5, Nova Lite, Nova Pro) plus Titan Embed Text v2. Enable model access for each in the account first.
- **Roles, not keys.** Remove `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` from the task definitions. The SDK finds the task role.

| Task role | Needs |
|---|---|
| `api` | invoke the embedding model (`bedrock:InvokeModel`); `s3:GetObject`, `PutObject`, `ListBucket` on the five buckets; read its secrets and decrypt with KMS |
| `harness` | `bedrock:InvokeModel` (the Converse calls) on the inference profiles and the foundation models behind them in every region the profiles span, and on the embedding model; S3 on the same buckets; its secrets |
| execution role | pull from ECR, write to CloudWatch Logs, read the secrets it injects |

Scope the Bedrock resource ARNs to exactly the profiles and models in `ai/models/tiers.yaml`; a cross-region profile needs the foundation-model permission in each region it routes to. The `us-west-2` region in `BEDROCK_REGION_FALLBACK` is read by no code.

- **Secrets Manager and KMS** hold what `.env` holds locally: the three Mongo URIs, `RABBITMQ_URL`, `SERVICE_TOKEN_SECRET` and the JWT private key. ECS injects them as environment variables. Identity reads its signing key from a **file** (`JWT_KEY_FILE`), and, as far as I know, Fargate cannot mount a secret as a file, so a start-up step must write the secret to a shared volume, or identity must be changed to take the PEM from the environment. Not built. There is one keypair and no overlap window (see [../runbooks/secrets-and-keys.md](../runbooks/secrets-and-keys.md)); a KMS-backed signing key is a better target.
- **People.** Identity authenticates its own seeded users against one shared demo password hash. That is a demo device (`DEMO_USERS_ENABLED`, honoured only while `ENV` is `local`, `test` or `ci`). A real deployment needs an OIDC identity provider (for example Cognito) behind identity or in place of it. Not built.

## Environment contract, variable by variable

| Variable (`.env.example`) | Local (compose) | On AWS |
|---|---|---|
| `ENV` | `local` | `prod` |
| `SEED_ON_EMPTY` | `true` | `false`; seed once with the one-off task |
| `MONGODB_URI`, `MONGODB_URI_AUDIT_WRITER`, `MONGODB_URI_EVALS_READER` | built by compose from the generated passwords | Secrets Manager; Atlas `mongodb+srv://` URIs |
| `RABBITMQ_URL` | `amqp://` from `RABBITMQ_DEFAULT_USER` and `_PASS` | Amazon MQ `amqps://` endpoint, from Secrets Manager |
| `OPA_URL` | `http://opa:8181` | `http://localhost:8181` (sidecar) |
| `IDENTITY_JWKS_URL`, `IDENTITY_VALIDATE_URL`, `CASE_SERVICE_URL` and the other service URLs | `http://api:8000/api/fcrm/...` | unchanged, with `api` as the Service Connect name |
| `GATEWAY_ORIGIN` | `http://localhost:8080` | the public `https://` origin |
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | MinIO | unset |
| `S3_REGION`, `S3_BUCKET_*` | `us-east-1`, five short names | the real bucket names |
| `AWS_DEFAULT_REGION` (and `AWS_REGION`) | `.env` | task environment |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | `.env` or the shell | none: the task role |
| `VECTOR_BACKEND`, `EMBEDDING_PROVIDER` | `memory`, `bedrock` | `memory`, `bedrock` (S3 Vectors: see above) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty, or the collector with `make up-obs` | `http://localhost:4318` (ADOT sidecar) |
| `REDIS_URL` | empty | leave empty (see below) |
| `SERVICE_TOKEN_SECRET`, `JWT_KEY_FILE` | `.env`, `jwt_keys/` | Secrets Manager; the file step above |
| `DEMO_USERS_PASSWORD_HASH`, `DEMO_USERS_ENABLED` | demo values | not for production |
| `LOGIN_RATE_LIMIT`, `CASEFLOW_PREFETCH`, `AGENT_PREFETCH`, `AGENT_MAX_TOOL_CALLS` | compose defaults | same defaults; tune with load |

## Networking

- One VPC, two AZs. ALB in public subnets; tasks, Amazon MQ and the Atlas PrivateLink endpoint in private subnets. Security groups: ALB to gateway on 8080, gateway to `api` and `web`, `harness` and `api` to Mongo, MQ and the endpoints; no inbound to `harness`.
- Interface endpoints for `bedrock-runtime`, `secretsmanager`, `kms`, `logs`, `ecr.api` and `ecr.dkr`; a gateway endpoint for S3. A NAT gateway only if something else needs the internet.
- **ALB idle timeout 120 s.** The default is 60 s. The examiner question waits up to 60 s for an answer (the gateway allows 75 s) and streams are held open with a heartbeat every 15 s (`SSE_HEARTBEAT_S`).
- **The gateway's `resolver 127.0.0.11`** in `ops/nginx/default.conf` is Docker's DNS. On ECS it must point at the VPC resolver. Not changed.

## Observability

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to an ADOT collector sidecar and use the `awsxray`, `awsemf` and `awscloudwatchlogs` exporters in place of the Jaeger and Prometheus ones. The receivers and the credential-redacting processor stay (`ops/otel/collector.yaml`, `ops/otel/env/prod.yaml`). JSON logs go to stdout and the `awslogs` driver. The three Grafana dashboards under `ops/grafana/provisioning/dashboards` read Prometheus metrics; on AWS rebuild them on CloudWatch or run managed Grafana. Not built.

## Known blockers and limits

1. **Redis does nothing.** `REDIS_URL` is a defined setting and `make up-cache` starts Redis, but the SSE hub is in-process and the Redis fan-out is documented in `fcrm_case/sse.py` as "not implemented". A second `api` task would not push live events to every client (a missed push is recovered by the log replay on reconnect and the web's polling). Run one `api` task, or implement the fan-out first.
2. **S3 Vectors is not in the image** (above).
3. **No resume from a handoff.** A run whose agent failed twice stays in `handoff` (`docs/governance/known-gaps.md`).
4. **Harness scale-out was never run.** Steps are idempotent and the relay holds a lease, so several `harness` tasks are designed for; only one was ever run. Start with one.
5. **No TLS in the compose stack.** TLS ends at the ALB in this design.
6. **No IaC, no CI/CD.** GitHub Actions is out of scope (ADR-15). Images would be built and pushed to ECR by hand or by CodeBuild.
7. **Single-key identity and a demo user directory** (above).

## Rough monthly cost shape

Assumptions, all mine and none checked against the AWS pricing pages: `us-east-1`, 730 hours a month, two availability zones, one `api` task, one `harness` task, the `gateway` and `web` at two tasks each, an M10-class Atlas cluster, Amazon MQ at its smallest size, 100 cases a month, no WAF rule groups. Use the AWS Pricing Calculator for real numbers.

| Line | Shape | Rough monthly |
|---|---|---|
| Fargate: `api`, `harness`, `gateway`, `web`, OPA sidecars | about 1 vCPU and 2 GB for each Python task, 0.25 vCPU and 0.5 GB for each nginx task | low hundreds of USD |
| ALB | one load balancer plus light traffic | tens of USD |
| NAT gateways and interface endpoints | two NATs, six interface endpoints in two AZs | roughly 100 to 200 USD; the largest avoidable line |
| MongoDB Atlas | M10-class replica set | tens of USD; a production tier is hundreds |
| Amazon MQ for RabbitMQ | smallest instance, or a three-node cluster for HA | tens of USD, or several hundred |
| S3, Secrets Manager, KMS, ECR, CloudWatch Logs, X-Ray | demo volumes | tens of USD together |
| **Bedrock** | **from the repo**: one demo case through all eleven agent jobs costs about USD 0.45 (`ops/scripts/demo_case_tokens.py`, `evals/README.md`); a sampled `make evals` run costs about USD 2.86 (`evals/results/2026-10-06/index.json`) | 100 cases is about USD 45; an eval run every night is about USD 85 |

Shape, not a quote: fixed infrastructure dominates. At a hundred cases a month the model spend is a small fraction of the bill, and what moves the total is the broker, the NATs and endpoints, and whether the database is a production tier. Token cost per case is the lever the repository actually optimises (`ops/reports/token-analysis.md`). The prices in `ai/models/tiers.yaml` are placeholders to be confirmed against the Bedrock pricing page.

## Order of work, if this were built

1. Terraform or CDK for the VPC, endpoints, ECR, the five buckets, Secrets Manager and the roles.
2. Atlas cluster with PrivateLink; run the init scripts; decide the RabbitMQ engine; import the definitions.
3. Build and push the three images (`backend`, `web`, `opa` with the policy baked in); write the JWT file step.
4. Task definitions with the variable table above; ALB, WAF, Route 53, ACM; the nginx resolver change.
5. Seed once, drive the demo case by hand (`RUNNING.md`), verify the audit chain, then decide whether the vector split and an OIDC provider are in scope.

## Checked and not checked

- Checked against the repository: every variable name against `.env.example`; the S3 credential-chain behaviour (`fcrm_common/objectstore.py`); that the S3 Vectors extra is outside the shipped image and conflicts with the agents (`src/backend/pyproject.toml`, `src/backend/Dockerfile`, `workers/context-worker/pyproject.toml`); the Mongo features used; that change streams are not used; the gateway behaviours (`ops/nginx/default.conf`); that Redis fan-out is not implemented; the Bedrock cost figures from the committed eval files.
- Not verified: DocumentDB compatibility, Amazon MQ engine versions and settings, every AWS price, ECS Service Connect behaviour with this nginx configuration, and any permission list beyond what the code calls. No AWS resource was created.
