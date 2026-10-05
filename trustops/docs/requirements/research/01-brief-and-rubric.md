# A. Problem statement, rules, rubric and TrustOps concept: detailed notes

Sources (all read in full):
- S1 `Genius Hacks Q3 2026.html`: event page printout (saved from myridius.com/genius-hacks-q3-2026)
- S2 `Genius_Hacks_Connect.pdf`: 4 slides (title, "What we expect from your solution", "The AI-native toolchain, phase by phase", Thank You)
- S3 `trascript - session1 - genius-hacks-3.txt`: kickoff call transcript. Only 1:23 to about 3:00 and 28:48 to 31:22 survive.
- S4 `TrustOps — How the Risk Assessment Workbench Works.html`: the team's solution concept page

Deadline note: S1 says the submission deadline is **30 September 2026**. The caller says it has moved to **9 October 2026**, and these notes use that date. Today is 2 Oct 2026.

---

## 1. Framing of the event (S1, S2, S3)

- Tagline: "One loosely defined business problem. Five weeks. Take it from a half-page brief to something production ready, using AI at every phase of delivery, and be ready to explain every decision you made along the way."
- "You will be judged on how you apply AI within each phase of delivery, govern it, evaluate it and decide where humans stay in the loop."
- "Participants will not be evaluated on the AI model they use. They will be evaluated on how effectively they design, orchestrate, govern, evaluate and operationalise AI-driven software delivery from requirement through production."
- The brief "is deliberately short and incomplete, because refining it is part of what is being assessed."
- FAQ: "do not wait for clarification that is not coming. Show us how your setup handles ambiguity."
- S3 (Rohit Suri): "this hackathon is different from the one that we have conducted previously. So the previous two hackathons, the focus was more on solving business problems, having a good polished demo and presentation." The recording cuts off there. By implication, the focus now is engineering and the AI harness, not a polished demo.
- S2 banner: "**Expect the panel to probe decisions, not features.** Every choice you made, whether a gate, a model, an agent boundary or a fallback to plain logic, should have a reason behind it."
- S1: "Expect the panel to probe decisions rather than features. Every choice you made should have a reason behind it."

## 2. The problem statement (S1, verbatim)

**Title:** "Risk Assessment Workbench: Automating financial crime risk assessment for new products and changes"

**The Client:** "A banking and financial services leader: a large national bank with roughly half a trillion dollars in assets, spanning consumer banking, commercial banking, payments and wealth management, closely supervised and examined on how well it controls financial crime risk."

**The Business Problem:** "Every time a business unit wants to launch a new product, add a feature, change a process, onboard a vendor, enter a new geography or open up to a new customer segment, the Financial Crimes Risk Management (FCRM) function must assess the financial crime risk it introduces before it goes live. The outcome decides whether it is approved, rejected, deferred or approved with conditions."

"Today that process runs on email, Word, Excel and SharePoint, and depends heavily on which analyst picks up the case and what they happen to remember. A single assessment takes fifteen to twenty business days, two analysts can reach different conclusions on the same change, and when an examiner asks months later why a risk was rated the way it was, reconstructing the answer is slow and often incomplete."

**What They Want Built:** "A Risk Assessment Workbench: one governed platform that carries a change request from intake through assessment to a committee decision, replacing the email and spreadsheet trail. Workflow, scoring and record-keeping are automated. AI is applied where it earns its place, typically finding relevant policy, pulling structure out of submitted documents and drafting the assessment for review. It serves three groups: a product owner who raises the change, an FCRM analyst who finalises the assessment and owns platform configuration, and a risk committee that votes on the result."

**Personas (from the brief):** Product Owner (raises the change), FCRM Analyst (finalises the assessment, owns platform configuration), Risk Committee (votes on the result).

**Six change triggers:** new product, new feature, process change, vendor onboarding, new geography, new customer segment.

**Four outcomes:** approved, rejected, deferred, approved with conditions.

**Pain points:** 15 to 20 business days per assessment; inconsistency between analysts; the reasoning cannot be reconstructed for examiners; the process depends on which analyst picks up the case and what they remember; the trail lives in email, Word, Excel and SharePoint.

**AI is suggested for:** (1) finding relevant policy, (2) "pulling structure out of submitted documents", (3) "drafting the assessment for review". Workflow, scoring and record-keeping are to be "automated", which does not have to mean AI.

**What Good Looks Like (verbatim bullets):**
- "Intake to decision-ready in around two days, against fifteen to twenty today"
- "Every rating traceable to its inputs and reasoning"
- "Foundational data for AI: a modelled, governed data layer the system can actually reason over"
- "Scoring parameters and workflow rules tunable by the risk function"
- "An immutable audit trail that can be handed to an examiner"
- "The system prepares, humans decide. Nothing is approved or rejected automatically"

**Constraints Worth Knowing (verbatim):**
- "Risk decomposition grounded in published supervisory frameworks, not invented. Finding the right ones is part of the exercise"
- "Controls mitigate risk; they never eliminate it. Scoring must respect that"
- "A human can disagree with any system output, record why, and have the consequences handled"
- "Synthetic data only. No connection to any real system"

**Deliberately Not Specified ("These are the decisions being assessed. Make them, and be ready to defend them."):**
- "Which parts of the workflow are automated, which use AI and which stay manual"
- "How risk is scored"
- "Where the human checkpoints sit"
- "Which stack you use"

**Data involved:** the brief says only "Synthetic data only." The FAQ adds: "Synthetic data only, generated by your team. No connection to any real system, and no real client or customer information of any kind." Implied data: submitted documents, policy corpus, cases, ratings and the audit trail. No dataset is provided.

## 3. Six SDLC stages that must be demonstrated (S1, verbatim)

"Your submission should show AI applied across all six stages of delivery, not just code generation."
1. **Requirements:** "Understanding the business problem, expanding a vague brief into a working specification and conducting the research needed to fill the gaps."
2. **Design:** "Solution and technical architecture, data model design, user experience decisions and the tools used to reach them."
3. **Development:** "Code generation, data engineering, agent orchestration and the tool chain that connects them."
4. **Testing:** "Validation, quality assurance and test automation."
5. **Deployment:** "Production deployment approach and go-live path."
6. **Operations:** "Monitoring, observability and continuous improvement."

S2 gives an expected output per phase:
| Phase | Suggested tools | Output |
|---|---|---|
| Requirements, "Expand the brief" | Deep research modes in Claude, ChatGPT, Gemini; NotebookLM; Perplexity | "a written spec in /docs" |
| Design, "Architecture & UX" | Figma, Excalidraw, Mermaid, draw.io, Miro | "diagrams + decision records" |
| Development, "Build & orchestrate" | Claude Code, Copilot, Cursor; LangGraph, CrewAI, Semantic Kernel, Agents SDK; pgvector, Azure AI Search | "working code + prompts in /ai" |
| Testing & Evals, "Prove it works" | Ragas, DeepEval, promptfoo, Langfuse, LangSmith; Playwright, pytest, Jest | "eval sets, scores, defect trail" |
| Deployment, "Ship it" | Docker, Kubernetes, Azure Container Apps; GitHub Actions, Azure DevOps pipelines | "a repeatable deploy path" |
| Operations, "Watch and improve" | Langfuse, Arize Phoenix, OpenTelemetry, Azure Monitor; token and cost dashboards | "monitoring + token analysis" |

## 4. Judging rubric (S1, verbatim). Total is 100%.

| Weight | Criterion | Description |
|---|---|---|
| **30%** | AI harness and agent orchestration | "Your architecture, how work is orchestrated, how context is managed, the data foundation the harness runs on, your instruction design, and how you divided the work between AI and people." |
| **20%** | SDLC automation | "How effectively AI is applied within each of the six delivery stages, and how those stages connect into a coherent flow rather than isolated experiments." |
| **15%** | Human-in-the-loop and governance | "Your review gates and the rationale for each. What risk is this gate controlling, and why is it placed here?" |
| **10%** | Evaluation framework | "How you know the output is good. What you measure, how you measure it, and how failures feed back into improvement." |
| **10%** | Context engineering and requirement expansion | "How you researched an incomplete requirement, layered in domain knowledge and maintained that context across the workflow." |
| **5%** | Production readiness | "Scalability, security, architectural standards and reliability. A demo that works once is not the bar." |
| **5%** | Token efficiency | "Which models you used, where consumption concentrated and what you did to optimise it." |
| **5%** | Engineering judgement | "Distinguishing deterministic from probabilistic problems, and justifying where you chose traditional logic over an LLM." |

**What earns full marks:** the sources give no scoring scale and no full-marks descriptors, only the weights and the descriptions above. From the descriptions, the strongest submission would:
- 30%: show a documented harness (architecture, orchestration, context management, data foundation, instruction design, AI/human split) as real artefacts in the repo.
- 20%: cover all six stages, and connect them ("a coherent flow rather than isolated experiments").
- 15%: give a rationale for every gate: what risk it controls and why it sits where it does.
- 10%: define metrics and measurement, show results, and show a failure-to-improvement loop.
- 10%: show research evidence, layered domain knowledge, and context kept across the workflow.
- 5% + 5% + 5%: show non-demo robustness, a token analysis with optimisations, and explicit deterministic-vs-LLM choices.

**"What we are not evaluating" (verbatim):** "Which AI tool or assistant you use"; "Who generates code fastest"; "Raw output volume"; "Polished looking output without underlying engineering".

**S2 "Six things the jury will look for in every solution":**
1. "Use of AI across all SDLC phases": "Requirements, design, development, testing, deployment and operations. Not code generation or demo alone"
2. "A governed harness": "Detailed agent architecture, orchestration, context management and instruction design"
3. "Human checkpoints with reasoning": "Where AI leads, where a person decides, what risk each gate controls and why"
4. "Evidence that it works": "Evals with defined measures, results you can show and a feedback loop after failures."
5. "A foundational data layer": "Modelled and governed data the solution is actually reason over. *Synthetic data only*" (sic)
6. "Production ready": "Scalability, security, reliability and token consumption analysis"

**Repository weight:** "Your prompts and agent configuration, your eval definitions and results, your architecture decisions and your commit history are the evidence behind roughly three quarters of the rubric."

## 5. Mandatory requirements and hard constraints

What the sources actually say, topic by topic:

- **Stack:** free choice. "The stack is your choice. Nothing here is required and the list is not exhaustive." You must be able to justify it.
- **Model:** free choice. "The model choice carries no marks on its own... you should be able to explain why you chose what you chose, including where you deliberately used traditional logic instead of an LLM."
- **Architecture / agents:** no mandated pattern. The rubric expects an agent harness: orchestration, context management, instruction design, and a data foundation. S2: "Detailed agent architecture".
- **AI across all six SDLC stages:** mandatory ("Your submission should show AI applied across all six stages").
- **Human decides:** "Nothing is approved or rejected automatically." "A human can disagree with any system output, record why, and have the consequences handled."
- **Scoring:** must be grounded in "published supervisory frameworks" and must respect "Controls mitigate risk; they never eliminate it."
- **Audit:** "immutable audit trail that can be handed to an examiner"; "Every rating traceable to its inputs and reasoning."
- **Configurability:** "Scoring parameters and workflow rules tunable by the risk function."
- **Data:** synthetic only, generated by the team, with no connection to any real system and no real client data.
- **"Production grade":** "Scalability, security, architectural standards and reliability. A demo that works once is not the bar." FAQ on deployment: "You need to demonstrate a credible deployment and operations approach... A running deployment is stronger evidence than a description of one, but the panel is assessing enterprise-grade thinking rather than uptime." So a live deployment is **not strictly mandatory**, but it is stronger evidence.
- **Evals:** expected (10%, plus a slide item). There must be "defined measures, results you can show and a feedback loop after failures." The `/evals` folder should hold "eval definitions, datasets and results". No specific eval tool is required.
- **Guardrails:** the sources never use the word. The closest are governance and review gates, the "nothing automatic" rule, and recorded overrides.
- **Hallucination control:** the sources never use the word. Related requirements are "Risk decomposition grounded in published supervisory frameworks, not invented" and "Every rating traceable to its inputs and reasoning." (TrustOps adds "Policy is retrieved, never generated".)
- **Security:** named only as part of production readiness (5%) and S2 item 06. The transcript mentions SAST (below). No specific security standard is named.
- **Observability:** Stage 06 Operations: "Monitoring, observability and continuous improvement". The output is "monitoring + token analysis", and `/ops` holds "deployment, monitoring, token analysis". Suggested tools: Langfuse, Arize Phoenix, OpenTelemetry, Azure Monitor, and token and cost dashboards.
- **Testing:** Stage 04 "Validation, quality assurance and test automation". `/tests` holds the "automated test suites".
- **Code quality tools / SAST:** these come only from the transcript (Akhil Jha, 29:00), said as encouragement rather than a stated rule: "But things like code quality, right? Being able to integrate. Tools like code quality tools, being able to integrate tools which give you, for example, SAS codes, right, or static security analysis of the code." No specific tool is named.
- **Documentation:** the repository layout in section 7, plus "a clear README matters as much as clean code". The README covers "setup, walkthrough, decision log". No document format is mandated beyond the folders and the README; Markdown is implied.
- **Commit history:** "Commit history is part of the evidence. A repository that appears fully formed in a single commit tells the panel very little about how you worked." This is effectively mandatory: commit incrementally.
- **Token efficiency:** a token consumption and efficiency analysis is required (rubric 5%, a deck item, and `/ops`).
- **Failure handling:** FAQ: "Agents will stall, ask for input they cannot get, or run without sufficient context. Be ready to explain how you detected the failure, how you recovered, and how your instructions and prompts evolved to become more robust as a result." This failure handling "is scored". It refers to the team's build agents ("fail mid-build"), not only the product's agents.

## 6. Allowed and disallowed

**Allowed or encouraged:**
- Any model or provider. Listed examples: Claude, OpenAI, Gemini, Azure AI Foundry, AWS Bedrock.
- Any orchestration framework. Examples: LangGraph, CrewAI, AutoGen, Semantic Kernel, OpenAI Agents SDK, Temporal.
- AI coding assistants, which are "actively encouraged": Claude Code, GitHub Copilot, Cursor, Windsurf. "AI-assisted coding inside a real codebase is a different thing entirely and is actively encouraged... The distinction is whether you are directing the work or accepting whatever a generator hands back."
- Retrieval tools: pgvector, Pinecone, Weaviate, Azure AI Search, Elasticsearch.
- Eval and observability tools: Langfuse, LangSmith, Ragas, DeepEval, Arize Phoenix (plus promptfoo and OpenTelemetry in S2).
- Testing tools: Playwright, pytest, Jest, k6.
- Deployment targets: Docker, Kubernetes, Azure Container Apps, AWS ECS, Vercel (plus GitHub Actions and Azure DevOps in S2).
- Design tools: Figma, Excalidraw, Mermaid (plus draw.io and Miro in S2).
- Research tools: deep research modes, NotebookLM, Perplexity (S2).
- Any team composition: "All Myridius employees globally". Be ready to explain why you composed the team the way you did.

**Disallowed:**
- "**One restriction: no prompt-to-app generators.** Platforms that generate a complete working application from a short prompt, such as Lovable, Bolt, v0 and Replit Agent, may not be used to produce your submission." S2 repeats this as "Not permitted".
- Real data or connections to real systems.
- Commits after the deadline: "Commits after this point are not assessed."

**Not addressed in the sources:**
- Reuse of existing or pre-written code, open-source libraries or templates. Nothing either way.
- External SaaS or APIs in general. Only the tool list (all external services) and "no connection to any real system".
- Whether paid or enterprise-licensed tools are expected.

## 7. Deliverables, submission mechanics, timeline

**Two deliverables**, both due by the deadline (originally "end of day 30 September 2026", now 9 Oct 2026 per caller): "Repository access and presentation due at end of day."

**1. GitHub repository (primary submission).** "One repository containing everything: the codebase and every artefact produced along the way. Judges will read it before and during your presentation." Expected layout:
- `/src`: application code
- `/ai`: prompts, agent config, guidance files
- `/docs/requirements`: the spec you expanded from the brief
- `/docs/architecture`: design decisions and diagrams
- `/docs/governance`: review gates and the rationale for each
- `/evals`: eval definitions, datasets and results
- `/tests`: automated test suites
- `/ops`: deployment, monitoring, token analysis
- `README.md`: setup, walkthrough, decision log

**2. Presentation deck.** It must cover seven things, "in whatever order works for your story":
1. The problem as given, and the specification you expanded it into
2. Your harness: architecture, orchestration and context management
3. Where AI leads, where a person leads, and the review gates between them
4. Your evaluation approach and what the results showed
5. A working demonstration of the solution
6. Failure handling and how the setup improved through iteration
7. Token consumption and efficiency analysis

**Demo video:** the sources do not mention one. The deck must include "A working demonstration", which is presumably live during the final presentation. Whether a recorded video is required or useful is an open question.

**Submission mechanics:** "access to a single GitHub repository" plus the deck. The sources do not say how to grant access (which accounts or org, public or private), where to upload the deck, or what format it should be in.

**Timeline:**
- Registration deadline: 2 Sep 2026
- Submission deadline: 30 Sep 2026 in S1, updated to 9 Oct 2026
- Final presentations: "To be announced". "Shortlisted teams present to the jury panel, followed by questions on their decisions." This implies a shortlist stage based on the repo and deck.
- Awards: "To be announced"

**Support:** mentors "help you interpret the problem statement and... offer guidance... They will not make your design decisions for you". S3: there is a common pool of mentors, not one per team, in a group with team captains, and "The captains can interact with the mentors". Other channels: the Teams channel, GeniusHacks@myridius.com (response within 24 hours), and regional PoCs.

## 8. The "end-to-end harness" theme (S3 + S2)

Akhil Jha, 28:48 to 29:49, verbatim (transcript quality is poor):
- "enable features, the experience layer, the insights you can generate out of it, right? So, the more richer you want to build the application, the more complex your engineering will be, right? Which is what you are being scored on, right? So, absolutely, Sai, right? Be creative, be think end to end, right?"
- "But things like code quality, right? Being able to integrate. Tools like code quality tools, being able to integrate tools which give you, for example, SAS codes, right, or static security analysis of the code. You can, you can think, I mean, there's no end to what you can do in this, right?"
- "End-to-end harness today, that what enterprises have started doing is they are building it in a way that you build it once and every... team will use it, right? And think of it this way, just 15 years back, right, when DevOps was still a thing, right, which was just starting to get standardized. Everyone was starting to do their own things. Then enterprises... started standardized to DevOps, right? Everyone then came to one single tool. That is where every company will ultimately land."
- "that they will have their own way of AI execution, it will be a connected end-to-end suite that will be standard for the enterprise. Now, within that, how far you can go is up to you, right? And that is what the entire hack competition is all about."

**What the organisers appear to mean:**
- The "harness" is the AI-driven **delivery** pipeline, the repeatable SDLC toolchain. It is not only the agents inside the product.
- It should be a connected, reusable suite, like a standardised DevOps toolchain: requirements research, design, codegen, tests and evals, CI/CD including quality and SAST gates, deployment, and monitoring.
- Other teams should be able to adopt it ("build it once and every team will use it").
- This matches the rubric. The 30% criterion explicitly includes "instruction design" and "how you divided the work between AI and people". The 20% criterion asks that the stages "connect into a coherent flow".
- The 30% criterion also covers "the data foundation the harness runs on", so the product's runtime agent architecture probably counts too. Both readings are plausible. Ask the mentors.
- Richer product features mean more complex engineering, "which is what you are being scored on".

## 9. TrustOps "How the Risk Assessment Workbench Works" (S4)

### Domain framing
- Six change types that need an assessment: "New product · New feature · Process change · Vendor or third-party onboarding · New geography · New customer segment. A single change often falls into several at once, and the assessment covers the union of them." These match the brief's six triggers.
- Two-stage method: 1. Inherent risk ("how likely... how bad"), 2. Controls (sanctions screening, CDD, transaction monitoring, vendor audits, each with an effectiveness rating), 3. Residual risk ("decides how much oversight").
- The floor: "at least 40 percent of inherent risk survives as residual." This implements the brief's "never eliminate" constraint.
- Four AML risk categories "drawn from the US regulatory examination framework" (FFIEC): customer or entity, product and service, geography, delivery channel and third party. Which apply depends on the change type. Trigger categories (6) are distinct from AML risk categories (4); the latter are "derived from them".
- Frameworks referenced: FFIEC (four categories), FATF (jurisdiction lists), OFAC (sanctions). "The regulatory frameworks referenced are real ones."

### Three failures and their design counterparts
- Incomplete submissions are answered by a "completeness gate at intake". Incomplete submissions are "the single largest contributor to the overall timeline".
- "Two analysts, two conclusions" is answered by a "shared policy layer with an evidenced scoring model".
- Unreconstructable reasoning is answered by "a case record that accumulates continuously rather than being filed at the end".

### Roles and visibility
| Role | Does | Cannot |
|---|---|---|
| Product Owner | Raises the case, describes the change, answers questions, receives the outcome | See the risk score, policy mapping, control ratings or reasoning. Configure the rules that gate their own change. |
| FCRM Analyst | Reviews, maps policy, rates controls, produces score and recommendation. **Owns all system configuration.** | Edit what the business submitted (they request clarification instead) |
| Risk Committee | Reads the generated pack, casts one vote with written rationale. **Three members, majority decides.** | Edit anything. Votes are hidden until all three are cast. |

### Flow: 17 steps across 4 swimlanes (PO, System/AI, Analyst, Committee)
1. PO: case opens. A case ID is issued before any content exists.
2. PO and System: documents first, questions second. **Extraction and classification run in parallel** on the uploads (business case, contract).
3. PO: categories confirmed before detailed questions, because the required fields depend on them. Removing a proposed category needs a recorded reason.
4. System and PO: prior cases offered for reuse. "Field-level reuse with checkboxes, never a whole-case clone." Reused values carry a source-case label.
5. System and PO: only the gaps are asked about. Answers go in chat or an inline box, both writing to the same field. A completion ring shows the threshold.
6. System: **validation gate**. "Four checks run visibly" (the four are not named). Submission needs **80% of required fields AND every always-required field**. A failure names its ground and reopens intake for only what is missing.
7. System: **auto-triage** assigns an analyst by workload-balanced rotation. Every case has a named owner from arrival.
8. Analyst: reviews summary, categories, flagged inconsistencies and precedent. **No score is shown yet**, to avoid anchoring.
9. Analyst: re-tags if categories are wrong. Adding a category expands the required set and can push the case back below the threshold.
10. Analyst and PO: clarification is **batched (one round), field-anchored, and pauses the analyst clock**. Answers write back as submission v2, with the original retained and a diff shown.
11. System: **analysis pipeline**. "Five agents run in sequence with the scoring computation between them": policy, risk, controls, precedent, narrative. "scoring computed, not inferred". Blocked below the threshold. Re-running discards prior work and requires a reason.
12. Analyst: confirms policy clauses (accept, remove with reason, or add by search) and control ratings (one click, or override with reason). "Per component, never in bulk."
13. System: score recomputes on each change. "Inspectable to four levels of depth, down to the arithmetic." An override that crosses a band boundary is flagged and logged with before, after and cause.
14. Analyst: recommendation confirmed and locked. Unlocking requires a reason and regenerates the pack. "Material clarifications assemble themselves from what was asked and what changed."
15. System and Analyst: **committee pack generated deterministically, no AI**. Narrative and notes are editable; numbers and ratings are locked. The plain-language PO summary must exist before the pack can be approved.
16. Committee: three votes, hidden until all are cast, each with a mandatory written rationale. Majority decides. Applies to bands 3 and 4.
17. PO: outcome returns with a reason, in business terms. Never the score, band, control ratings or clauses.

Bands 1 and 2 instead go to "Delegated close" by the analyst. "Filing is not a step": the record accumulates every agent action, every override with its reason, every clarification round, every version, and the versions of the scoring model, field set and policy corpus in force.

### Scoring engine (deterministic, server-side module, "no AI touches it")
Inputs:
- Likelihood 1 to 5 and Impact 1 to 5: set by the analyst against written definitions. Some values are pre-populated from reference data, such as a jurisdiction risk list.
- Control effectiveness (CE) 0.0 to 10.0 per control: proposed by the system from an inventory with evidence, then confirmed or overridden.
- Control weights: sum to exactly 1.00 per category.
- Category weights: relative, normalised across in-scope categories.

```
Inherent  = L × I                       // 1..25
CE        = weighted mean of category control ratings
Retention = 1 − (0.6 × CE ÷ 10)         // 1.00 .. 0.40
Residual  = Inherent × Retention
Composite = Σ(Residual × normalised category weight)
Band      = lookup(Composite); Route = band's routing consequence
```

Bands:
| Band | Composite | Route |
|---|---|---|
| 1 | 0.0 to 5.0 | Delegated close |
| 2 | 5.1 to 10.0 | Delegated close |
| 3 | 10.1 to 17.0 | Committee |
| 4 | 17.1 to 25.0 | Committee |

"Four bands over two routing outcomes is deliberate" for reporting granularity.

Engine guards:
- **Boundary proximity:** a composite within 0.5 of a band edge is flagged and needs analyst acknowledgement.
- **Nothing is retroactive:** each case records the versions it was scored under. Config changes affect new work only, with an explicit, logged option to re-score.
- **Evidence shelf life:** artifacts have test dates. Evidence is flagged stale or nearing stale; a rating with no artifact is "judgment-only".
- **Planned controls do not count:** they are recorded separately and affect no score.

### Worked example: payout partner for consumer remittances in a new corridor
The change is a vendor onboarding, a new geography and a new product at once, so three categories are in scope.
- Delivery channel/third party: L4, I5, inherent 20, CE 7.6, retention 0.54, residual 10.9, weight 0.45
- Geography: L4, I4, inherent 16, CE 6.0, retention 0.64, residual 10.2, weight 0.30
- Product/service: L3, I4, inherent 12, CE 6.5, retention 0.61, residual 7.3, weight 0.25
- Composite 9.8, Band 2, delegated close. The boundary flag fires.
- The analyst finds the vendor due-diligence audit predates the corridor and covers domestic operations only. They raise a clarification and cut CE from 7.6 to 4.5 with a reason. Retention becomes 0.73 and residual 14.6.
- New composite 11.5, Band 3, committee. (I checked the arithmetic: it is consistent.)

### Design rules (verbatim headings)
- "AI proposes, humans decide": per-component confirmation; "A single accept-all button would make the audit trail record consent instead of review."
- "Scoring is arithmetic, not inference": "No model ever sets a rating or a band."
- "Policy is retrieved, never generated": clauses are shown verbatim with source. If none apply, the system says so. "A fabricated policy citation in a bank's risk record is worse than no citation."
- "The business owner cannot configure their own gate"
- "Submissions are never edited by the analyst"
- "Overrides carry reasons": covers category removal, clause removal, control rating change, and re-run.
- "Visibility is asymmetric by design": the PO view "renders nothing at all rather than a locked placeholder".
- "Closed means closed": no one can re-run or edit a closed assessment.

### What is built (S4's own statement)
- **Real:** the deterministic scoring engine; field resolution, band lookup, routing, version stamping, completeness and dashboard aggregation, all computed "from a generated data corpus".
- **Mocked:** "All AI agent outputs are scripted. There is no database, no authentication, and no live retrieval. The persona switcher stands in for real sessions." It is a front end over a mock service layer that has one function per backend call and fakes latency.
- **Planned sequence:** DB and API, then the scoring engine moved server-side, then agents one at a time, with **policy retrieval first**. (Note: the engine is described as "server-side in its own module" but the prototype is front end only, so today it presumably runs client-side. This is an internal inconsistency.)
- **SLA:** business days, on two clocks: total elapsed, and analyst-active (paused while awaiting the business).

### Where S4 differs from, or falls short of, the problem statement and rubric
1. **AI is fully mocked, with no DB, no auth and no retrieval.** The rubric puts 30% on the AI harness and agent orchestration, asks for a "modelled, governed data layer", and wants production readiness ("A demo that works once is not the bar"). The prototype as described would score poorly on these until it is made real.
2. **Outcomes:** the brief lists four outcomes (approved, rejected, deferred, approved with conditions). S4 describes routing (delegated close vs committee) and a majority vote, but never names the outcome set, how conditions are captured, or how a 1-1-1 split across the four outcomes resolves.
3. **Delegated close for bands 1 and 2.** It is a human analyst decision, so it is consistent with "Nothing is approved or rejected automatically". But the route is determined automatically and the brief says the workbench carries a request "to a committee decision". Be ready to defend this.
4. **Configuration ownership:** the brief says the analyst "owns platform configuration", and S4 matches ("Owns all system configuration"). Neither describes a config UI, the approval of config changes, or segregation of duties. Who governs the analyst's own config changes is an open governance gap.
5. **The two-day target:** the brief's "around two days" goal is not explicitly addressed in S4, which mentions SLA clocks only.
6. **Supervisory frameworks:** S4 grounds its categories in FFIEC, FATF and OFAC, which fits "finding the right ones is part of the exercise". The likelihood and impact definitions and the band thresholds are not sourced from any framework, and the 40% floor is called "a real principle in the domain" with no citation.
7. **SDLC harness, evals, ops, token analysis, CI/SAST:** S4 does not cover them. It describes only the product, while about 75% of the rubric is about the delivery harness and repo artefacts.
8. **"Immutable" audit trail:** S4 describes an accumulating record, but with no DB it has no immutability mechanism yet (append-only store, hashing, and so on).
9. **Validation gate:** "Four checks" are not enumerated.
10. **Band gaps:** the band table jumps from 5.0 to 5.1, 10.0 to 10.1 and 17.0 to 17.1. Rounding and precision are undefined (where does 5.04 go?).

## 10. Ambiguities and open questions for mentors

1. Confirm the new deadline (9 Oct) and its time zone and cut-off ("end of day" where?). Do commits after the original 30 Sep count?
2. Submission mechanics: how to grant repo access (which GitHub accounts or org)? Can the repo be private? Deck format and upload location? Is there a submission form?
3. Is a recorded demo video expected or allowed, in addition to the live "working demonstration" in the deck?
4. "AI harness" scope: is the 30% about the SDLC delivery harness (how we build), the product's runtime agent architecture, or both? How should they be weighted?
5. Is a live cloud deployment expected, or are a reproducible Docker or local deploy and a documented go-live path enough? Will judges run the code themselves?
6. Are code-quality and SAST tooling (mentioned on the call) expected or graded, and under which criterion: production readiness or SDLC automation?
7. Reuse of existing code: may we reuse our own pre-built components, internal accelerators (for example an internal AIDLC pipeline), templates or OSS starter kits? Is code from before registration allowed?
8. Is there a preferred or provided LLM access (Azure OpenAI, Bedrock, Claude enterprise keys), or a budget cap? Are paid external services OK given "no connection to any real system"?
9. Can public regulatory documents (FFIEC manual, FATF lists, OFAC SDN) be used as the policy corpus, or must the bank's internal "policy" be synthetic? Is public regulatory data "real data"?
10. Does "the system prepares, humans decide" allow analyst-delegated closure of low-risk cases without the committee, or must every case reach a committee vote?
11. How should the four outcomes (approved, rejected, deferred, approved with conditions) map to committee voting? Tie-breaks? Conditions tracking after approval?
12. What does "decision-ready in around two days" mean for evals: should we measure simulated cycle time?
13. Will the panel see a token-usage format or metric baseline (cost, tokens per case)? Should the analysis cover build-time tokens (coding assistants), runtime tokens, or both?
14. What is the shortlisting process and the final presentation duration and format? Is the demo live or recorded?
15. Do judges expect evidence of team roles and AI usage per person (the FAQ says "be ready to explain why you composed your team the way you did")?
16. Are there expected auth or RBAC standards (SSO mock acceptable?) for the "production readiness" security bar?
17. Are there any rubric descriptors or score levels beyond the weights (what earns full marks)?
