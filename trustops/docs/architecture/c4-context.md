# C4: System context

```mermaid
C4Context
  title TrustOps — system context

  Person(po, "Product Owner", "Raises a case; answers questions; never sees a score")
  Person(analyst, "FCRM Analyst", "Reviews every AI proposal item by item; owns configuration")
  Person(committee, "Risk Committee", "Votes on Band 3-4 cases by hidden ballot")

  System(trustops, "TrustOps", "FCRM Risk Assessment Workbench: case intake, AI-assisted review, deterministic scoring, committee decision, immutable audit trail")

  System_Ext(bedrock, "Amazon Bedrock", "Claude and Nova models; Titan embeddings")
  System_Ext(s3vectors, "Amazon S3 Vectors", "optional vector backend for policy_clauses; not in the shipped image (in-memory vectors by default, ADR-49)")
  System_Ext(aws, "AWS (S3, deploy target)", "File storage; the operator's own deployment, documented not built by this run")

  Rel(po, trustops, "Uses", "HTTPS")
  Rel(analyst, trustops, "Uses", "HTTPS")
  Rel(committee, trustops, "Uses", "HTTPS")
  Rel(trustops, bedrock, "Calls for every agent, via Pydantic AI")
  Rel(trustops, s3vectors, "Hybrid search + upsert")
  Rel(trustops, aws, "Files; documented AWS deploy path")
```

TrustOps is the only system in scope. AEO/EVOQ components are cloned into this repository and modified here (ADR-05); the running AEO platform is never called at runtime, so it does not appear as an external system above.
