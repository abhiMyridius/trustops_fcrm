# C4: Containers

```mermaid
C4Container
  title TrustOps — containers

  Person(po, "Product Owner")
  Person(analyst, "FCRM Analyst")
  Person(committee, "Risk Committee")

  Container_Boundary(web, "Web") {
    Container(w1, "fcrm-web", "React 19 + Vite", "Role-aware UI for all three personas; the harness monitor")
    Container(g1, "gateway", "nginx", "/api/fcrm/* routing; auth_request to identity")
  }

  Container_Boundary(services, "HTTP services (FastAPI, one Mongo DB each)") {
    Container(s1, "case (S1)", "FastAPI", "Cases, submissions, documents, review items, threads, conditions, SSE")
    Container(s2, "scoring (S2)", "FastAPI", "The deterministic engine; routing rules")
    Container(s3, "policy (S3)", "FastAPI", "Corpus, controls, jurisdictions, field sets, hybrid search")
    Container(s4, "audit (S4)", "FastAPI", "Evidence ledger; hash-chained audit trail")
    Container(s5, "decision (S5)", "FastAPI", "Packs, hidden votes, tally, outcomes")
    Container(x1, "identity (X1)", "FastAPI", "Login, JWT issuance, JWKS")
  }

  Container_Boundary(harness, "Harness (FastStream workers)") {
    Container(h1, "caseflow (H1)", "FastStream", "The state machine; gates; budgets; outbox")
    Container(h2, "agent-worker (H2)", "FastStream + Pydantic AI", "One model call per job, behind 7 guards")
    Container(h3, "ingestion-worker (H3)", "FastStream", "Documents to Markdown with page:line anchors")
    Container(h4, "context-worker (H4)", "FastStream", "Embeddings; the three S3 Vectors indexes")
  }

  ContainerDb(d1, "MongoDB 8", "one database per service")
  ContainerDb(d2, "MinIO / S3", "uploads, canonical docs, packs")
  Container(x2, "OPA", "Open Policy Agent", "Route authz; agent-to-tool permits")
  ContainerQueue(x3, "RabbitMQ", "case.events, agent.jobs, ingestion.jobs, context.jobs")
  ContainerDb(x7, "Redis", "cache only — JWKS, OPA decisions, SSE fan-out")
  System_Ext(bedrock, "Amazon Bedrock")
  System_Ext(s3v, "Amazon S3 Vectors")

  Rel(po, w1, "HTTPS")
  Rel(analyst, w1, "HTTPS")
  Rel(committee, w1, "HTTPS")
  Rel(w1, g1, "HTTPS")
  Rel(g1, s1, "/api/fcrm/cases/*")
  Rel(g1, s2, "/api/fcrm/scoring/*")
  Rel(g1, s3, "/api/fcrm/policy/*")
  Rel(g1, s4, "/api/fcrm/audit/*")
  Rel(g1, s5, "/api/fcrm/decision/*")
  Rel(g1, x1, "/api/fcrm/auth/*; auth_request")

  Rel(s1, x3, "publishes case.events")
  Rel(x3, h1, "consumes")
  Rel(h1, h2, "agent.run / agent.done via agent.jobs")
  Rel(h1, s1, "HTTP")
  Rel(h1, s2, "POST /score")
  Rel(h1, s4, "HTTP: audit records")
  Rel(h1, s5, "HTTP: packs/outcomes")
  Rel(h2, bedrock, "model calls")
  Rel(h2, x2, "tool permit check")
  Rel(h3, d2, "stores canonical docs")
  Rel(h4, s3v, "embeds + indexes")
  Rel(s3, s3v, "hybrid search")

  Rel(services, d1, "reads/writes its own DB")
  Rel(services, x2, "route authz")
  Rel(s1, x7, "SSE fan-out cache")
```

Every arrow above is real today as a Pydantic contract or a passing test (`fcrm_contracts`, the nine packages' pure logic), a pure function, or a config file (`ops/`, `ai/`) — the actual network calls are the remaining HTTP/Mongo/RabbitMQ wiring, landing once `fcrm_common` is merged.
