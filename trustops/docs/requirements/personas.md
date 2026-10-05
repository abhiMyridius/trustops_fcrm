# Personas

Three roles, one workbench. Each role's screen set and data access are defined in `docs/governance/visibility-matrix.md`; this document is who they are and what they're trying to do.

## Product owner (`fcrm_po`)

**Who:** the business owner proposing a change — a new product, a vendor relationship, a new corridor, a feature, a process change, or a new customer segment. In the canonical demo: **Priya Nair**, Payments.

**Goal:** get a change approved (or get clear, actionable feedback) as fast as possible, with as little specialised compliance knowledge required as the process can manage. Today this takes 15–20 days; the target is about two.

**What they do:** upload supporting documents, confirm or correct what the intake agent extracted, answer a short batch of clarification questions if the analyst needs more, submit, and then wait — reading updates in plain language, never in risk terminology. They never see a score, a band, a control rating, or an agent's proposal before it's confirmed.

**What frustrates them today:** vague rejections with no reasoning, being asked the same question twice, and not knowing where in the process a case actually is.

## FCRM analyst (`fcrm_analyst`)

**Who:** the financial-crimes risk management analyst who reviews every AI proposal, confirms or overrides it, and either closes a case (Band 1–2) or hands it to committee (Band 3–4). In the demo: **Rahul Iyer**.

**Goal:** make a defensible, evidence-grounded risk call quickly, without rubber-stamping the AI's work. They own all configuration — the field sets, control inventory, policy corpus, jurisdiction list, scoring model and routing rules — but never edit what the business submitted.

**What they do:** review the completeness report and ask one batched round of clarifying questions if needed; accept, override or remove each proposed policy clause, risk factor and control rating one at a time, always with a reason for an override; resolve critic findings; draft and lock a recommendation; release a pack to committee or close the case directly.

**What frustrates them today (per the prototype teardown):** a UI that lets a "bulk accept" happen in spirit even when no single button says so, completion percentages that disagree with each other, and a config screen that can change the very field set or scoring model a case was judged against after the fact.

## Risk committee member (`fcrm_committee`)

**Who:** a senior risk officer who votes on Band 3–4 cases. Three members in the demo: **Ana Mendes, Joseph Okafor, Lin Chen**.

**Goal:** decide on a complete, trustworthy, unchanging pack — never on live data that might shift under them — and have their vote actually stay hidden until everyone has voted.

**What they do:** read the pack (the locked recommendation, the score, the conditions), ask a question if something is unclear (which becomes a visible addendum, not a side conversation), and cast one of four votes — approve, approve with conditions, defer, reject — with a written rationale. If no two votes agree, nothing is decided automatically: a new round opens.

## A fourth, non-human "persona": the eight rules and R1–R6

Every screen and every agent operates inside the same eight rules and six routing rules (`docs/governance/rules.md`). They are not a persona's preference — they are the constraints every persona's workflow is built against, and the panel is told to probe exactly these: a gate, a model, an agent boundary, a fallback to plain logic.
