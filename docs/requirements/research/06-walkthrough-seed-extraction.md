# F · Walkthrough v2 embedded seed (`window.FCRM_SEED`): structure, values and scoring check

Source: `/Users/apahuja/Downloads/Hackathon/Documents/04-walkthrough.html`, 355,712 bytes, 809 lines. `cmp` confirms it is byte-identical to `Documents/04-walkthrough-v2.html`.

Everything below is read from the file. Lines marked "(my calculation)" or "(observation)" are my own arithmetic or inference.

Working copies are in the scratchpad `v2/` folder:
- `seed.clean.json`: the seed, pretty-printed.
- `recompute.py`: the scoring recompute.
- `wt_script.txt`: the walkthrough script with line numbers.

---

## 0. Where the seed lives and how it is used

- **Line 248** holds `<script>window.FCRM_SEED={…};</script>`. It is one line of about 240,600 characters of JSON, which is about 68% of the file.
- **Lines 249–808** hold the player as a single IIFE:
  - L252: `var PROTO=window.FCRM_PROTO===true;`
  - L254: `var SEED=window.FCRM_SEED, D=SEED.demo, CASE=D.id, BY={};`
  - L262: `SB=D.score_before, SA=D.score_after`.
  - The scenes run from L334 to L772.
  - The scenes are pushed through `scene({label,dur,base,actions})`.
- **Audit numbers come from the seed.** `AR(type,scene,k)`, `ARrange(scene)` and `ARlast(scene)` (L272–274) look up record numbers in `D.audit` by `type` and `scene`. The code comment says this is "so the counter, the log and the examiner never disagree". `syncCounters(k)` sets the audit counter from the seed whenever you jump to a scene.
- **What the seed drives:**
  - The dashboard KPIs (PO_ACTIVE, PO_WAIT, PO_COND, `derived.avg_days_intake_to_decision`).
  - The analyst queue (open cases for `u-riyer`) and the fast-lane row.
  - The precedent row, the fields, clauses, control rows, score boxes, conditions, the committee question, the votes, the examiner answer and the reference chips.
  - The versions table, the agent-quality table, and the watchlist re-flags (open cases whose `geographies` intersect `jurisdictions.fixture.added`).
- **Hard-coded, not taken from the seed:**
  - Token increments: 17 literal `tokens(n)` calls, totalling 16,900 (my calculation).
  - Most monitor wording, and several literals: "23 of 23 claims", "2.7" deviation, "8f3a…c21e" hash, "0h 25m", diff timestamps "2026-10-01 09:40 / 14:05", confidences 0.94/0.91/0.83.
- **Prototype mode.** If `window.FCRM_PROTO=true` is set before the script runs:
  - The cursor is hidden and the header span changes to "· prototype".
  - `note()`, `monNote()` and `cursorTo()` do nothing.
  - `click()` fires its handler after 650 ms with no cursor animation.
  - The code comment reads: "prototype mode: no cursor, no callouts; screens, monitor, captions and timeline unchanged".
  - (Observation) Scenes still run their scripted actions on timers. "Click-through" therefore means stepping through scenes with Back and Next; the screens themselves are not interactive.

## 1. Top-level structure

| Key | Contents |
|---|---|
| `meta` | generated 2026-10-01 · generator `gen_seed.py` · scoring v3 · field_set v6 · controls v9 · corpus v14 · jurisdictions v2026-06 · routing v4 · prompts 2026-09-28 · note "Synthetic. Every band is recomputed from its inputs with the scoring model; any row can be checked by hand." |
| `scoring_model` | v3 (see §4) |
| `domains` | 4 domains |
| `change_types` / `type_domains` | 6 types |
| `field_set` | v6: 6 always-required fields, 21 per-type fields |
| `controls` | 24 controls (inventory v9) |
| `corpus` | v14: 9 sources, 197 clauses |
| `jurisdictions` | `current` v2026-06 plus `fixture` v2026-10-fixture |
| `agents` | 9 agents |
| `guards` | 7 guards |
| `rules` | 8 rules |
| `budgets` | per tier, and per case |
| `routing` | v4, rules R1–R6 |
| `users` | 5 PO · 3 analyst · 3 committee |
| `demo` | 37 keys (§3) |
| `cases` | 80 cases, FCRM-2026-0072 to FCRM-2026-0151 (includes the demo case) |
| `derived` | dashboard and queue figures |
| `evals` | weekly figures per agent |

## 2. Reference data

### 2.1 Domains and change types
- **Domains:**
  - customer "Customer and entity"
  - product "Product and service"
  - geography "Geography"
  - channel "Delivery channel and third party"
- **Change types (to domains):**
  - new_product "New product" → product
  - new_feature "New feature" → product
  - process_change "Process change" → product, customer
  - vendor_onboarding "Vendor / third-party onboarding" → channel
  - new_geography "New geography" → geography
  - new_segment "New customer segment" → customer

### 2.2 Field set v6
- **Settings:** `max_required` 18 · `completion_threshold` 0.8.
- **Always required (6):**
  - F01 Change owner
  - F02 Change description
  - F03 Go-live date
  - F04 Customer segment
  - F05 Expected annual volume
  - F06 Partner or vendor identity ("when a third party is involved")
- **Per type:**
  - vendor_onboarding: F10 Partner licence number · F11 Partner AML programme attestation · F12 Contract AML clauses reference
  - new_geography: F20 Corridor countries · F21 Payout channels · F22 Local licensing status · F23 Settlement currency
  - new_product: F30 Product sign-off reference · F31 Transaction limits and velocity · F32 Expected monthly transaction count · F33 Disclosure text reference · F34 Monitoring rule coverage
  - new_feature: F40 Feature owner · F41 Affected products · F42 Data handled
  - process_change: F50 Process owner · F51 Affected controls · F52 Data handled
  - new_segment: F60 Segment definition · F61 Onboarding standard applied · F62 Expected customer count

### 2.3 Control inventory v9 (24 controls: id · domain · name · catalogue weight · evidence artefact · last tested)
| ID | Name | Weight | Evidence artefact | Last tested |
|---|---|---|---|---|
| CTL-G01 | Jurisdiction screening at onboarding | 0.20 | screening config export | 2026-05-14 |
| CTL-G02 | Corridor enhanced due diligence | 0.20 | EDD procedure and sample | 2026-03-02 |
| CTL-G03 | Sanctions list screening (OFAC / UN / EU) | 0.28 | screening test report | 2026-07-21 |
| CTL-G04 | Local licensing confirmation | 0.12 | regulator confirmation letter | 2025-12-09 |
| CTL-G05 | Country risk rating refresh | 0.20 | rating methodology and run log | 2026-06-30 |
| CTL-T01 | Vendor due-diligence assessment | 0.28 | DD report | 2023-11-15 |
| CTL-T02 | Payout-agent transaction monitoring | 0.16 | monitoring rule set | 2026-05-06 |
| CTL-T03 | Third-party contract AML clauses | 0.12 | executed contract | 2026-08-28 |
| CTL-T04 | Agent KYC file sampling | 0.24 | sampling programme report | 2026-06-18 |
| CTL-T05 | Fourth-party (subcontractor) oversight | 0.20 | subcontractor register | 2026-02-11 |
| CTL-P01 | Transaction limits and velocity rules | 0.24 | limits configuration | 2026-04-22 |
| CTL-P02 | Product sign-off | 0.12 | sign-off record | 2026-09-10 |
| CTL-P03 | Monitoring rule coverage | 0.20 | rule coverage matrix | 2026-05-06 |
| CTL-P04 | Disclosure and consent | 0.12 | disclosure text and approval | 2026-01-30 |
| CTL-P05 | Fee transparency | 0.12 | fee schedule review | 2025-11-20 |
| CTL-P06 | Post-launch risk review (OCC 2017-43) | 0.20 | review calendar and last report | 2026-07-01 |
| CTL-C01 | KYC / CDD at onboarding | 0.18 | procedure and QA sample | 2026-06-12 |
| CTL-C02 | Enhanced due diligence for high-risk customers | 0.14 | EDD case sample | 2026-04-03 |
| CTL-C03 | PEP screening | 0.12 | screening test report | 2026-07-21 |
| CTL-C04 | Periodic KYC refresh | 0.12 | refresh completion report | 2026-03-31 |
| CTL-C05 | Beneficial ownership verification | 0.12 | BO verification sample | 2026-02-19 |
| CTL-C06 | Adverse media screening | 0.10 | screening vendor attestation | 2026-05-27 |
| CTL-C07 | Segment attestation | 0.10 | attestation register | 2026-08-05 |
| CTL-C08 | Source of funds verification | 0.12 | SoF procedure and sample | 2026-01-15 |

Per-domain weight sums: geography 1.00 · channel 1.00 · product 1.00 · customer 1.00 (my calculation). In a case, the weights are re-normalised over the controls that matched.

### 2.4 Policy corpus v14
- **Ingested:** 2026-09-22.
- **Total:** 197 clauses across 9 sources.

| ID | Title | Kind | Clauses | Used for |
|---|---|---|---|---|
| FFIEC | FFIEC BSA/AML Examination Manual (BSA/AML Risk Assessment · CDD · Third-Party Payment Processors · Electronic Banking · Funds Transfers · Nonbank FIs / MSBs · Foreign Correspondent Accounts (summary)) | public | 86 | Risk domains, inherent-risk factors, expected controls |
| OFAC | OFAC sanctions programme requirements (summarised) (Screening obligations · List types · 50% rule) | public | 14 | Sanctions screening control expectations |
| FINCEN-2026 | FinCEN AML/CFT program rule (proposed Apr 2026, summarised) | public | 9 | Why changes are assessed at all |
| OCC-2017-43 | OCC Bulletin 2017-43 (summarised) | public | 11 | Conditions and follow-up after approval |
| FATF | FATF high-risk and increased-monitoring lists | public | 2 | Geography risk, watchlist re-flag |
| INT-FCRM-4.2 | Internal FCRM Policy 4.2 · High-Risk Corridor Jurisdictions (§1–§4) | synthetic | 18 | Geography controls |
| INT-TP-7.1 | Internal Third-Party AML Standard 7.1 (§1 Scope · §2 Payout agents · §3 Agent KYC standard · §4 Contract clauses) | synthetic | 22 | Vendor and agent controls |
| INT-PR-2.3 | Internal Product Risk Standard 2.3 | synthetic | 15 | Product and service controls |
| INT-CO-1.8 | Internal Customer Onboarding Standard 1.8 | synthetic | 20 | Customer and entity controls |

### 2.5 Jurisdictions
- **`current` v2026-06.** Published 2026-06-19; source "FATF plenary, 17–19 June 2026".
  - Call for action (3): DPRK, Iran, Myanmar.
  - Increased monitoring (22): Angola, Bolivia, Bosnia and Herzegovina, Bulgaria, Cameroon, Côte d'Ivoire, DRC, Haiti, Iraq, Kenya, Kuwait, Lao PDR, Lebanon, Monaco, Nepal, Papua New Guinea, South Sudan, Syria, Venezuela, Vietnam, Virgin Islands (UK), Yemen.
  - Changes: added Bosnia and Herzegovina, Iraq; removed Algeria, Namibia.
  - Mexico is on neither list.
- **`fixture` v2026-10-fixture.**
  - `fixture: true`; added Algeria, Namibia; removed none.
  - Note: "Synthetic test list for the watchlist re-flag demo. Not an FATF publication. Re-lists the two jurisdictions removed in June 2026 so that two open seeded cases are affected."
  - The two re-flagged cases: FCRM-2026-0144 "North Africa trade finance (Algeria)" (awaiting_business) and FCRM-2026-0147 "Payroll partner (Namibia)" (under_assessment).

### 2.6 Agents (9: name · tier · model → fallback · tools · skills · output · gate · eval set)
| Agent | Tier | Model → fallback | Tools | Skills | Output | Gate | Eval set |
|---|---|---|---|---|---|---|---|
| screening | cheap | anthropic:claude-haiku → openai:gpt-4.1-mini | field_rules, precedent_search | declare_gap | ScreeningProposal (fast_lane: bool, reasons) | analyst confirms fast lane | screening/fast-lane-80 |
| intake | cheap | anthropic:claude-haiku → openai:gpt-4.1-mini | document_reader, precedent_search, jurisdiction_list, field_rules | cite_verbatim, declare_gap | ExtractionProposal[] · CategoryProposal[] · GapQuestion[] | PO confirms each | intake/extraction-80 |
| research | strong | anthropic:claude-sonnet → openai:gpt-4.1 | policy_search, precedent_search, jurisdiction_list | cite_verbatim, declare_gap, compare_precedent | ClauseProposal[] · PrecedentMatch[] | analyst accepts each clause | research/clauses-80 |
| assessment | strong | anthropic:claude-sonnet → bedrock:anthropic.claude-sonnet | control_inventory, evidence_store, scoring_engine | cite_verbatim, compare_precedent | FactorProposal (L, I, justification) · ControlProposal[] | analyst confirms each rating | assessment/ratings-80 |
| critic | strong + judge | openai:gpt-4.1 → anthropic:claude-sonnet | evidence_ledger, precedent_search, rules_check | compare_precedent | CriticFinding[] (type, severity, record refs) | analyst explains or adjusts | critic/planted-errors |
| narrative | mid | anthropic:claude-sonnet → openai:gpt-4.1-mini | case_record, evidence_ledger | draft_for_business_reader, batch_questions | CompletenessReport · Question[] · Narrative · Condition[] · BusinessSummary | analyst decides whether to ask first; edits and locks | narrative/judge |
| examiner | mid | anthropic:claude-sonnet → openai:gpt-4.1-mini | audit_log | cite_verbatim, declare_gap | ExaminerAnswer (sentences each with record ids) | none · read-only | examiner/grounding |
| watchlist | cheap | anthropic:claude-haiku → openai:gpt-4.1-mini | jurisdiction_list, case_search | — | ReflagProposal[] | analyst acknowledges | watchlist/reflag |
| policy_gap | mid | anthropic:claude-sonnet → openai:gpt-4.1-mini | policy_search, case_search | declare_gap | GapSuggestion[] | policy owner reviews | policy_gap/suggestions |

(Observation) The model strings are family aliases, not pinned version IDs.

### 2.7 Guards, rules, budgets, routing
- **Guards (7):**
  1. input scan: injection patterns, personal data masked
  2. permit: OPA, may this agent call this tool
  3. budget: tokens, wall time, retries, fallback order
  4. output shape: schema valid, every claim has a source
  5. rules as code: the eight rules before any state change
  6. critic: a second model checks sources, precedents, rules, recency
  7. human gate: a person accepted or overrode each item
- **Rules (8):** No agent wrote a score · No bulk accept · PO view has no restricted fields · Analyst never edited the submission · Closed case untouched · Override has a reason · Planned control not scored · Policy quoted, not generated.
  - (Observation) This list does not match the glossary's "eight rules". The seed has "No bulk accept" and "Planned control not scored". The glossary has "AI proposes, humans decide" and "the PO cannot set her own gate" instead.
- **Budgets:**
  - cheap: 6,000 tokens per run, 60 s wall time, 1 retry
  - mid: 12,000 tokens, 120 s, 1 retry
  - strong: 20,000 tokens, 180 s, 1 retry
  - "strong + judge": 20,000 tokens, 180 s, 1 retry
  - per case: 120,000 tokens
- **Routing v4:**
  - R1: Band 1 or 2, the analyst closes under delegated authority.
  - R2: Band 3 or 4, the Risk Committee decides; quorum 3; simple majority; votes hidden until all are cast.
  - R3: Boundary flag (within 0.5 of the next band edge); the analyst must acknowledge before release.
  - R4: Fast lane. The screening agent proposes it when the only change type is a vendor renewal or a feature with no new geography, segment or product and annual volume under $5M. The analyst confirms; Band 1 is expected.
  - R5: Auto-triage. New cases go to the analyst with the fewest open cases.
  - R6: Committee question to the analyst. The thread is visible to all members and the answer is attached as a pack addendum. A number change requires unlock with a reason, pack v2, and votes re-requested.

### 2.8 Users
- **PO:**
  - u-pnair Priya Nair (Payments)
  - u-tgold Tom Goldberg (Wealth)
  - u-skim Sarah Kim (Cards)
  - u-mros Marcus Rossi (Commercial)
  - u-ldia Lucía Díaz (Digital Assets)
- **Analysts:** u-riyer Rahul Iyer · u-mshah Meera Shah · u-dbrooks Daniel Brooks.
- **Committee:** u-amendes Ana Mendes · u-jokafor Joseph Okafor · u-lchen Lin Chen.

## 3. The demo case (`demo`)

### 3.1 Identity
- **Case:**
  - ID **FCRM-2026-0151**.
  - Title "Consumer remittances US → Mexico via payout partner"; short form "Mexico remittances via payout partner".
  - Unit Payments.
- **People:** owner u-pnair (**Priya Nair**, Product Owner · Payments); analyst u-riyer (**Rahul Iyer**, FCRM Analyst).
- **Committee:** **Ana Mendes** (the persona), **Joseph Okafor**, **Lin Chen**.
- **Deal:** partner **Remesas del Norte S.A.**; corridor **US → Mexico**; settlement currency MXN.
- **Change types:** vendor_onboarding, new_geography, new_product. Domains: channel, geography, product.
- **Trace ID:** `0af3c1e9d7b24c6e`.
- **Versions pinned:**
  - scoring v3
  - corpus v14
  - controls v9
  - field_set v6
  - jurisdictions v2026-06
  - routing v4
  - prompts 2026-09-28
- (Observation) The demo's own row in `cases[]` has `stage: draft`, `decided: null`, `geographies: []` (Mexico is not listed) and a score of 9.8 / Band 2, which is the before-override value.

### 3.2 Documents
| Name | Pages | MB | Candidates | PII masked | Note |
|---|---|---|---|---|---|
| business_case_remittance_MX.pdf | 14 | 2.1 | 11 | 3 | |
| payout_partner_contract.pdf | 9 | 0.8 | 4 | 2 | |
| vendor_DD_scope_letter_2026.pdf | 2 | 0.2 | — | — | added in v2 |

### 3.3 Fields (18 required = 6 always + 3 vendor + 4 geography + 5 product)
- **Counts:** `candidates_found` 15 · `filled_by_extraction` 9 · `filled_after_reuse` 12 · `filled_at_submit` 15.
- **Completion:** 9/18 = 50% → 12/18 = 67% → 15/18 = 83% (my calculation; this matches the rings on screen).

| ID | Field | Value | Source | How |
|---|---|---|---|---|
| F01 | Change owner | Priya Nair, Payments | case p.1 l.2 | extracted |
| F02 | Change description | Consumer remittances US → Mexico through payout partner Remesas del Norte S.A. | case p.1 l.3 | extracted |
| F03 | Go-live date | Q1 2027 → **February 2027** at review (`review_edit`: source_after "owner declared · v1", original_kept "Q1 2027 (case p.1 l.9)") | case p.1 l.9 | extracted, then owner-corrected |
| F04 | Customer segment | Retail consumers | case p.2 l.8 | extracted |
| F05 | Expected annual volume | $48M year 1 | case p.6 tbl 2 | extracted (2 candidates) |
| F06 | Partner or vendor identity | Remesas del Norte S.A. | contract p.1 | extracted |
| F20 | Corridor countries | US → Mexico | case p.4 l.12 | extracted |
| F23 | Settlement currency | MXN | contract p.3 §4 | extracted |
| F12 | Contract AML clauses reference | contract §9 (AML, screening, audit rights) | contract p.7 §9 | extracted |
| F31 | Transaction limits and velocity | $2,999 per transaction · $10,000 per 30 days | reused · FCRM-2026-0118 | reused |
| F33 | Disclosure text reference | Standard remittance disclosure v3 | reused · FCRM-2026-0118 | reused |
| F34 | Monitoring rule coverage | Remittance rule set v3 (structuring, velocity, corridor anomalies) | reused · FCRM-2026-0118 | reused |
| F10 | Partner licence number | CNBV-TR-2041-88 | owner answer 1 | answered |
| F32 | Expected monthly transaction count | About 62,000 per month by month 6 | owner answer 2 | answered |
| F21 | Payout channels | Bank deposit; cash pickup at partner agents | owner answer 3 · contract p.5 (bank deposit only) | answered; `new_fact` "cash pickup not in the business case" |
| F11 | Partner AML programme attestation | — | — | open (v2: "Corridor review scheduled Nov 2026 · scope letter attached", shown in the scene 12 diff) |
| F22 | Local licensing status | — | — | open |
| F30 | Product sign-off reference | — | — | open |

### 3.4 Precedents
- **Reuse source:** FCRM-2026-0118 "Payout partner onboarding (Guatemala corridor)", similarity 0.81. Closed 2026-06-15, approve_with_conditions.
- **Five closest** (CTL-T01 rating in each case):

| Case | CTL-T01 rating |
|---|---|
| 0118 | 4.5 |
| 0097 (Philippines) | 5.2 |
| 0084 (Colombia) | 4.0 |
| 0109 (El Salvador) | 5.5 |
| 0076 (Dominican Republic) | 5.3 |

- Their mean is 4.9, which equals `precedent_vdd_avg` 4.9 (my calculation). 7.6 − 4.9 = 2.7, the deviation the critic reports.

### 3.5 Policy clauses (4 accepted) and the gap
1. FFIEC-TPPP-04: FFIEC BSA/AML Manual · Third-Party Payment Processors. "…banks should assess the risks of the processor's customer base, transaction volumes and geographic reach…" (FFIEC 2024 ed. · p.312)
2. FCRM42-3.1: Internal FCRM Policy 4.2 · High-Risk Corridor Jurisdictions. "…new corridors require jurisdiction screening at onboarding and enhanced due diligence where the destination is rated High…" (Policy 4.2 §3.1 · v2026-03)
3. TP71-2.3: Internal Third-Party AML Standard 7.1 · Payout Agents. "…the bank shall obtain an independent assessment of the agent network's AML controls covering the specific corridor…" (Standard 7.1 §2 · v2025-11)
4. OFAC-1.2: OFAC Sanctions Screening Standard. "…screen all parties to a cross-border payment against the SDN and sectoral lists at initiation and on list update…" (OFAC std §1 · v2026-01)
- **Gap:** "No clause for cash-pickup agent networks in corpus v14".

### 3.6 Risk areas, controls, L × I and scores
Domain weights in scoring v3: customer 0.20, product 0.20, geography 0.24, channel 0.36. Re-normalised over the three domains in scope, they become channel 0.45 · geography 0.30 · product 0.25.

**Before override (`score_before`): composite 9.8 · Band 2 · route analyst · boundary_flag true · boundary_distance 0.3**

| Area | L | I | Inherent | Controls (rating · normalised weight · evidence · date) | CE | Retention | Residual | Area weight | Contribution |
|---|---|---|---|---|---|---|---|---|---|
| channel | 4 | 5 | 20 | CTL-T01 7.6 · 0.35 · DD report, audit 2023-11, domestic scope · 2023-11-15 · **stale**; CTL-T02 6.8 · 0.20 · monitoring rule set v3 · 2026-05-06; CTL-T03 7.0 · 0.15 · contract §9 · 2026-08-28; CTL-T04 8.4 · 0.30 · sampling programme report (existing partners) · 2026-06-18 | 7.59 | 0.54 | 10.9 | 0.45 | 4.9 |
| geography | 4 | 4 | 16 | CTL-G01 6.3 · 0.25 · screening config export · 2026-05-14; CTL-G02 4.0 · 0.25 · EDD procedure; no MX corridor sample yet · 2026-03-02; CTL-G03 8.5 · 0.35 · screening test report · 2026-07-21; CTL-G04 3.0 · 0.15 · CNBV confirmation not yet received · 2025-12-09 | 6.0 | 0.64 | 10.2 | 0.30 | 3.07 |
| product | 3 | 4 | 12 | CTL-P01 7.5 · 0.30 · limits configuration (reused from 0118) · 2026-04-22; CTL-P02 5.0 · 0.15 · sign-off record pending · 2026-09-10; CTL-P03 6.8 · 0.25 · rule coverage matrix · 2026-05-06; CTL-P04 7.0 · 0.15 · disclosure text v3 · 2026-01-30; CTL-P05 5.0 · 0.15 · fee schedule review · 2025-11-20 | 6.5 | 0.61 | 7.3 | 0.25 | 1.83 |

That is 13 rated controls in total (4 + 4 + 5). CTL-T05, CTL-G05, CTL-P06 and all CTL-C* controls are not matched.

**After override (`score_after`): composite 10.4 · Band 3 · route committee · boundary_flag true · boundary_distance 0.3**
- **Channel changes:**
  - CTL-T01 rating 4.5. Override: from 7.6, to 4.5, by Rahul Iyer, reason "Audit predates the corridor arrangement and covers domestic operations only. Corridor-specific due diligence is not yet evidenced."
  - Channel CE 6.51 · retention 0.61 · residual 12.2 · contribution 5.49.
  - A second entry is added with `id: "CTL-T01"`, `rating: null`, `planned: true`, evidence "corridor DD review scheduled Nov 2026 (scope letter)", note "planned control, not scored", weight_norm 0.0. (Observation) The planned control reuses the overridden control's ID.
- **Geography and product:** unchanged (3.07 and 1.83).

### 3.7 Critic, completeness, clarification
- **Critic findings:**
  1. precedent_deviation, high: "Vendor due-diligence rating 7.6 is 2.7 above the 4.9 average across the five closest payout-partner cases".
  2. evidence_recency, high: "Only evidence is a 2023 audit of domestic operations; corridor not covered".
  - Both carry `records: ["#30"]`. (Observation) #30 is the research proposal; CTL-T01's rating is in #31, the assessment proposal.
- **Completeness report:**
  - missing: none
  - stale: "Partner audit Nov 2023, domestic scope"
  - contradictory: "Cash pickup channel (owner answer 3) vs business case (bank deposit only)"
  - questions_drafted 3 · questions_kept 2
- **Questions (round 1):**
  1. Anchored to CTL-T01 / F11. "Is a corridor-specific due-diligence review of Remesas del Norte planned or complete? Please attach the scope and date if available." Answer: "A corridor review is scheduled with the auditor for Nov 2026. Scope letter attached." (doc vendor_DD_scope_letter_2026.pdf)
  2. Anchored to F21. "Please confirm the cash-pickup agent locations and whether agent onboarding follows the partner's KYC standard." Answer: "38 agent locations in 6 states. Agents onboarded under the partner's KYC standard v4."
  3. Dropped by narrative: "Confirm contract AML clause coverage." Reason: "already answered in contract §9".

### 3.8 Recommendation, conditions, committee
- **Recommendation:** outcome approve_with_conditions; edits_by_analyst 2.
  - Text: "Residual risk sits in Band 3, driven by the third-party channel. Sanctions screening and transaction monitoring are effective and evidenced. Vendor due diligence is not yet evidenced for the corridor; a review is scheduled for Nov 2026. Recommend approval conditional on that review completing before go-live and enhanced payout-agent monitoring for six months."
- **Conditions:**

| ID | Text | Owner | Due | Status | From |
|---|---|---|---|---|---|
| CND-1 | Corridor-scope vendor DD review completed and filed | Priya Nair | before go-live | open | overridden control CTL-T01 |
| CND-2 | Enhanced monitoring rules on payout agents live | FCRM Ops | go-live | scheduled | planned control |
| CND-3 | Quarterly sample review of agent KYC files | Priya Nair | 6 months | scheduled | policy gap (cash-pickup agents) |

- **Committee question:**
  - Ana Mendes asked: "Was the Nov 2026 scope letter reviewed by FCRM, or only received? Does its scope cover the cash-pickup agent network?"
  - Rahul Iyer answered: "Reviewed. The scope covers Remesas del Norte's corridor operations including the agent network (letter §2). It does not cover the agents' own KYC files, which is why condition CND-3 exists."
- **Votes:**
  - Joseph Okafor: approve_with_conditions (rationale 31 words).
  - Ana Mendes: approve_with_conditions, rationale "Channel risk is real but the conditions address the evidence gap directly. Approve subject to DD review before go-live."
  - Lin Chen: defer (rationale 27 words).
- **Tally and outcome:** approve_with_conditions 2 · defer 1 → outcome **approve_with_conditions**.

### 3.9 Examiner
- **Question:** "Why was FCRM-2026-0151 routed to committee instead of closed by the analyst?"
- **Seven sentences:**
  1. The composite residual was first computed at 9.8, Band 2, which would have closed under delegated authority [32].
  2. The boundary flag was raised because 9.8 sits 0.3 below the Band 3 edge [33].
  3. The critic flagged the vendor due-diligence rating of 7.6 as 2.7 above the precedent average of 4.9, with evidence limited to a 2023 domestic-scope audit [34].
  4. Analyst Rahul Iyer overrode the rating to 4.5 with the reason "audit predates the corridor arrangement" [49].
  5. The engine recomputed to 10.4, Band 3, which routes to committee under routing rules v4 [50].
  6. The band change and boundary flag were acknowledged [51].
  7. The committee approved with conditions by 2 to 1 [63, 64].
- Every cited record matches its type in `audit` (my check).

### 3.10 Audit records (67; `audit_count` 67; fields n · type · by · what · scene)
| Scene | Records |
|---|---|
| 2 | #1 case.created (S1) · #2 workflow.started (H1, field set v6) |
| 3 | #3–#4 document.stored (S1) · #5–#6 guard.input (P5: file 1 clean, 3 PII masked; file 2 clean, signatures masked) · #7 guard.permit (X2: intake → document_reader, precedent_search, jurisdiction_list, field_rules) · #8 proposal.extraction (15 candidates · 9 fields) · #9 proposal.category (3 types) · #10 guard.output (15 of 15 sourced) |
| 4 | #11–#13 gate.decided (PO confirmed vendor onboarding / new geography / new product) · #14 fieldset.resolved (18 fields) |
| 5 | #15 precedent.matched (0118, 0.81) · #16 values.reused (3) |
| 6 | #17 proposal.gaps (6 open · 3 blocking · 3 questions) · #18 owner.answered (3) · #19 fact.flagged (cash pickup) |
| 7 | #20 owner.corrected (go-live Q1 2027 → February 2027) · #21 gate.validation (4 of 4) · #22 case.submitted (v1 · 15 of 18) · #23 triage.assigned (Rahul Iyer, 4 open cases, lowest load) · #24 eval.recorded (X6) |
| 8 | #25 screening.proposed (fast lane: no) · #26 workflow.stage (assessment core, budget 120k) · #27 guard.permit · #28 guard.output (research attempt 1 rejected: 1 clause without verbatim quote) · #29 agent.retry (fallback model; budget 23% used) · #30 proposal.research (4 clauses · 5 precedents · 1 gap) · #31 proposal.assessment (L/I per area · 13 controls · 1 stale) |
| 9 | #32 score.computed (9.8 · Band 2) · #33 boundary.flagged (0.3 below Band 3 edge) · #34 critic.findings (2 on CTL-T01 · 8 of 8 rules) · #35 completeness.report |
| 10 | #36 clarification.sent (2 questions before review · analyst clock paused) |
| 11 | #37 owner.answered (v2 · 2 fields · 1 doc) · #38 guard.input (scope letter clean) · #39 agent.rerun · #40 control.planned |
| 12 | #41–#44 gate.decided (clauses FFIEC-TPPP-04, FCRM42-3.1, TP71-2.3, OFAC-1.2) · #45 gap.recorded |
| 13 | #46–#48 gate.decided (controls CTL-T04 8.4, CTL-T02 6.8, CTL-T03 7.0) |
| 14 | #49 human.override (CTL-T01 7.6 → 4.5, by S4, hash-chained) · #50 score.recomputed (10.4 · Band 3 · committee) · #51 gate.decided (band change and boundary acknowledged) |
| 15 | #52 proposal.narrative · #53 guard.output (PO summary, 0 restricted terms) · #54 recommendation.locked (2 edits) · #55 pack.assembled (S5, pack v1) |
| 16 | #56 gate.validation (4 of 4 release checks) · #57 pack.approved (released to 3) |
| 17 | #58 committee.question (S1) · #59 committee.answer (S5, addendum, numbers unchanged) · #60 vote.cast (1 of 3) |
| 18 | #61–#62 vote.cast (2 and 3 of 3) · #63 vote.revealed (2 · 1) · #64 outcome.set (3 conditions activated) |
| 19 | #65 outcome.delivered · #66 case.closed · #67 chain.verified |

Scenes 1, 20 and 21 write no records. (Observation) #60 (vote 1 of 3, Okafor's) is numbered after the question and answer (#58–59). On screen, scene 17 already opens with "1 of 3 votes in", and the handover says one colleague "has already voted".

## 4. Scoring model v3 and the recompute

**Model.**
- Likelihood scale: 1 Remote · 2 Unlikely · 3 Possible · 4 Likely · 5 Almost certain.
- Impact scale: 1 Minimal · 2 Minor · 3 Moderate · 4 Major · 5 Severe.
- Retention = max(0.4, 1 − 0.6 × CE / 10).
- Bands:
  - 1 "Low": 0.0–5.0, route analyst
  - 2 "Moderate": 5.1–10.0, route analyst
  - 3 "Elevated": 10.1–17.0, route committee
  - 4 "High": 17.1–25.0, route committee
- Boundary flag 0.5.
- Rounding: "composite rounded to one decimal before band lookup".
- `planned_controls_scored: false`.
- Owner FCRM Analytics; approved 2026-08-12.

**Method** (D-42 and the seed). For each area:
1. Inherent = L × I.
2. CE = Σ(catalogue weight ÷ sum of matched weights × rating).
3. Residual = inherent × retention.

Composite = Σ(domain weight normalised over the domains in scope × residual).

**Recompute (my calculation, `v2/recompute.py`):**

| | Channel CE | Ret | Residual | Geo residual | Product residual | Composite (raw) | Rounded | Band |
|---|---|---|---|---|---|---|---|---|
| Before | 0.35·7.6 + 0.20·6.8 + 0.15·7.0 + 0.30·8.4 = **7.590** | 0.5446 | 10.892 | 16 × 0.64 = 10.240 (CE 6.000) | 12 × 0.61 = 7.320 (CE 6.500) | 0.45·10.892 + 0.30·10.24 + 0.25·7.32 = **9.803** | **9.8** | **2** |
| After | 0.35·4.5 + … = **6.505** | 0.6097 | 12.194 | 10.240 | 7.320 | 5.487 + 3.072 + 1.830 = **10.389** | **10.4** | **3** |

- **The published numbers reproduce exactly:** 9.8 / Band 2 → 10.4 / Band 3. The monitor's recompute line is "CE 7.6 → 6.5 · retention 0.54 → 0.61 · residual 10.9 → 12.2 · composite 9.8 → 10.4". The decision log's D-42 and the walkthrough figures match it. Every area-level value stored in the seed (CE, retention, residual, weight, contribution) matches to its stored precision.
- **Robustness:** using rounded residuals gives 9.79 → 9.8 and 10.375 → 10.4, so the result does not depend on rounding order.
- **Boundary convention:** distance is measured to the lower bound of the higher band. Before: 10.1 − 9.8 = 0.3. After: 10.4 − 10.1 = 0.3, so the flag stays on. The 18 boundary-flagged seeded cases follow the same convention (e.g. 5.0 → 0.1, 5.6 → 0.5, 10.2 → 0.1). (Observation) R3 says "next band edge"; after the override the 0.3 is to the edge just crossed.
- **All 80 seeded cases** recompute to their stored composite and band, with 0 mismatches. The recomputed band distribution, 1:19 · 2:49 · 3:11 · 4:1, equals `derived.band_distribution`. Every case's `route` matches its band (1–2 analyst, 3–4 committee).
- **Old numbers superseded:**
  - The old docs had 9.8 → 11.5 with retention 0.54 → 0.73 and residual 10.9 → 14.6; that treated the 4.5 as the whole area's CE.
  - The old C recompute was ≈12.2. That was the channel *residual*, now confirmed as 12.2, not the composite.
  - The prototype code's 9.6 → 10.2 is not used anywhere in v2.

## 5. The 80-case corpus and derived figures
- **Fields per case:** id, title, unit, owner, analyst, change_types, domains, created, submitted, decided, stage, outcome, fast_lane, inputs (per area: L, I, controls[id, rating, evidence, evidence_date]), score (areas, composite, band, route, boundary_flag, boundary_distance), open_conditions, geographies.
- **Stages:** closed 65 · under_assessment 8 · awaiting_business 4 · committee 2 · draft 1 (the demo).
- **Outcomes of closed cases:** approved 38 · approve_with_conditions 23 · deferred 3 · rejected 1.
- **Fast lane:** one case, FCRM-2026-0140 "KYC vendor renewal" (Band 1, Rahul's queue).
- **`derived`:**
  - avg_days_intake_to_decision 3.3. My recompute over the 65 closed cases is 3.32; created and submitted are the same date in every closed case.
  - closed_cases 65.
  - po_active_cases [0139, 0146, 0150, 0151].
  - po_open_conditions 3.
  - analyst_open:
    - u-riyer [0138, 0140, 0143, 0146] (4)
    - u-mshah [0136, 0139, 0142, 0145, 0148] (5)
    - u-dbrooks [0137, 0141, 0144, 0147, 0150] (5)
  - band_distribution {1:19, 2:49, 3:11, 4:1}.
- **Screen values computed from the cases (my calculation from the walkthrough code):**
  - PO dashboard: **5** active (0150 Instant account-to-account transfers; 0146 Merchant onboarding API; 0139 Wealth referral partner, awaiting; 0121 SME lending, Texas, 2 conditions open; 0109 Payout partner onboarding (El Salvador), 1 condition open) · **1** awaiting · **3.3 d** · **3** open conditions.
    - (Observation) The walkthrough's "active" filter counts closed cases with open conditions, so it shows 5. `derived.po_active_cases` lists 4.
  - Analyst queue: "My queue · 5 cases": 0151 (new), 0146 Merchant onboarding API (Feature, assessing), 0143 Card controls in app (Feature, awaiting business), 0140 KYC vendor renewal (Vendor, Fast lane), 0138 Crypto custody pilot (Product · Segment, committee).
- (Observation) In 13 cases the `domains` field holds more domains than `type_domains` implies; for example, 0118 (vendor + geography) also has product. The type-to-domain map does not explain those extras.

## 6. Weekly evals (`evals`, week 2026-09-28, 80 golden cases, 0 regressions, "no change against 2026-09-21")
| Agent | Metric | n / correct | % |
|---|---|---|---|
| intake | extraction accuracy | 1388 / 1338 | 96.4 |
| research | citation faithfulness | 329 / 326 | 99.1 |
| assessment | override rate (lower is better) | 212 proposals / 38 overridden | 17.9 |
| critic | planted-error detection | 60 / 55 | 91.7 |
| narrative | judge: grounded, no restricted terms | 80 / 78 | 97.5 |
| examiner | every sentence cites a record | 120 / 120 | 100.0 |
| screening | fast-lane precision | 80 / 77 | 96.2 |
| watchlist | re-flag recall | 24 / 24 | 100.0 |
| policy_gap | suggestion acceptance | 14 / 11 | 78.6 |

All the percentages recompute from their counts (my calculation; 77/80 = 96.25, shown as 96.2).

## 7. Figures hard-coded in the walkthrough, not in the seed (and checks)
- **Tokens:** scene 3 2.7k · 5 0.6k · 6 0.7k · 8 6.4k · 9 1.6k · 11 1.1k · 12 0.3k · 15 1.4k · 20 2.1k. The run total is **16.9k**; the recap prints the live counter.
  - (Observation) Scene 8 logs "Guard 3 budget: 23% of the case budget used". 23% of 120k is ≈27.6k, but the monitor counter at that moment is ≈8.4k and the whole run uses 16.9k (my calculation).
- **Scene 8 retry:** "1 of 5 clauses had a paraphrase". The fallback shown is `SEED.agents[2].fallback` = openai:gpt-4.1; wall time 41 s.
- **Override record:** "hash 8f3a…c21e · prev #48".
- **Diff timestamps:** v1 2026-10-01 09:40; v2 2026-10-01 14:05, by Priya Nair.
- **Clock:** analyst clock "0h 25m" at the clarify scene.
- **Dates:** the walkthrough's `TODAY` is 2026-10-01. Due dates are 2 business days ahead.
