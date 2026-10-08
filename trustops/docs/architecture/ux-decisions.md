# User experience decisions

The brief's design stage asks for "user experience decisions and the tools used to reach them". This page collects them in one place. The screen-by-screen detail is design C (`design/C-web-tests-ops-sdlc.md`, sections 1 and 2, written 3 October); where the built web departs from it, the web in `src/frontend` and the decision log win. Who the users are is in `docs/requirements/personas.md`; who may see what is in `docs/governance/visibility-matrix.md`.

## How the decisions were reached

```mermaid
flowchart LR
  BA["BA's FCRM_v2 prototype<br/>React, 15 screens, 3 roles<br/>(sibling folder, reference only)"] --> TD["Prototype teardown<br/>05-prototype-teardown.html<br/>research/04-prototype-fcrm-v2-notes.md"]
  WT["Walkthrough v2<br/>21 scenes, click-through<br/>(window.FCRM_PROTO=true)"] --> SEED["Seed P7<br/>every figure on screen"]
  TD --> C["Design C, sections 1-2<br/>Claude Code plan subagent"]
  SEED --> C
  C --> G1{"G-D1<br/>operator accepts ADRs"}
  G1 --> MSW["Web built against MSW<br/>mock backend from the seed"]
  MSW --> R["Web route names become<br/>the API contract (ADR-30)"]
  MSW --> T["vitest-axe, contrast test,<br/>Playwright scenes"]
  BG["Myridius brand guide<br/>(7 October)"] --> SKIN["Re-skin, look and feel only<br/>(ADR-69)"]
  MSW --> SKIN
```

| Tool | What it was used for | Where it is |
|---|---|---|
| The BA's React prototype (FCRM_v2) | The information architecture: which screens each role has, the tabs inside a case, the first design tokens, the domain components worth keeping | `../FCRM_v2` (outside this repo); its analysis in `docs/requirements/source/05-prototype-teardown.html` and `docs/requirements/research/04-prototype-fcrm-v2-notes.md` |
| Walkthrough v2 (the BA and architect, 1 October) | The 21-scene story every screen has to tell, with the users, cursor path and numbers. It also runs as a click-through prototype | `docs/requirements/source/04-walkthrough.html`; the scene list in `docs/requirements/research/06-walkthrough-seed-extraction.md` |
| Claude Code plan subagents | Turned the teardown, walkthrough and decision log into the routing table, component library, state rules, error states and accessibility baseline of design C | `design/C-web-tests-ops-sdlc.md` |
| MSW mock backend generated from the seed | A clickable web before any service existed (`npm run dev:msw`); case 0151 is a live state machine in the mock, so the whole story can be walked and tested | `src/frontend/src/mocks` |
| The Myridius brand guide | The look: typeface, palette, logo, button and input shapes, the wave ornament. Applied on 7 October as a re-skin that changed no copy, DOM role or aria attribute | ADR-69; `src/frontend/src/design/tokens.css`, `public/brand-mark*.png` |
| Mermaid | Every diagram in `docs/architecture` | this folder |
| vitest-axe, an accessibility pass in the live Playwright sweep, a contrast test | Checked the accessibility decisions instead of only stating them | `src/frontend/src/test/axe.ts`, `src/frontend/e2e/sweep-live/06-a11y.spec.ts`, `src/frontend/src/test/design/contrast.test.ts` |

Figma and Excalidraw, which the brief suggests, were not used: a working React prototype already existed, and a coded mock against the real seed was a better test of the screens than static frames.

## The decisions

| # | Decision | Why | Where it holds | Ref |
|---|---|---|---|---|
| UX-1 | **Three separate workspaces, one per role**, each with its own navigation and home: the owner (Dashboard, My Queue, a case with six tabs, Integrations), the analyst (Dashboard, Queue, an eight-tab workbench, Assurance, Config), the committee (a queue and a three-tab case). Each role has a home of its own, `/po`, `/analyst` and `/committee`, and `/` sends a signed-in person to theirs. A role that opens another role's URL lands on its own home, not a 403 page | Each persona has a different job (`personas.md`). Redirecting rather than refusing means nothing on screen hints at what another role can see | `src/frontend/src/app/router.tsx`, `app/guards/HomeRedirect.tsx` | design C 1.2, ADR-69 |
| UX-2 | **The owner never sees a score, band, rating, vote or pack**, and the screen has no locked placeholders where they would be: the fields are absent, not hidden | Owners get plain-language progress, not risk terms. A hidden field can leak through the network tab, so the response shape itself has no such field | `features/po/**` may not import score-bearing code (ESLint); `case_po` contracts; S1's PO guard | ADR-32, D-62 |
| UX-3 | **Sign-in is a real login, and "switch user" is a re-login**. The prototype's persona switcher, which re-rendered one case under another role's permissions, is gone. In demo mode "Sign in with corporate SSO" opens a picker of seeded users. Signing out on purpose lands on a plain sign-in page; only an expired session brings the person back to where they were | A client-side role toggle cannot demonstrate a server-side fence | `features/auth/LoginPage.tsx`, `auth/AuthProvider.tsx` | ADR-10, ADR-69, design C C-05 |
| UX-4 | **One decision per item, no bulk accept.** Every clause, factor, control rating and critic finding has its own accept, override or remove action | The analyst must not rubber-stamp the AI's work (rule 2); the prototype allowed a bulk accept in spirit | Policy and Risk & Controls tabs; Guard 7 | BA rule 2, D-19 |
| UX-5 | **Every override, removal, re-run, unlock or vote carries a typed reason**, entered in one shared `ReasonDialog` that shows what the change does before it is saved. The web asks for 20 characters; the server refuses fewer than 10 | One component means one rule everywhere. The preview (S2's `/simulate` for a control override) shows the analyst the band change before they commit. The server's floor is lower so the guard never refuses what S1 already accepted | `components/domain/ReasonDialog.tsx`, `components/ui/Form.tsx` (`REASON_MIN`) | BA rule 6, ADR-22 |
| UX-6 | **No source, no claim.** Every extracted or proposed value carries a visible `SourceChip` (document page and line, clause, record) and a `ProvenanceTag` (Extracted, Reused from case, Owner answer, Owner declared, Analyst override, Jurisdiction list) | Analysts trust a proposal they can check in one click. A label is always visible, never a tooltip, so it survives a screenshot in an audit | `components/domain/SourceChip.tsx`, `ProvenanceTag.tsx` | BA rule 8 |
| UX-7 | **The live score is withheld until the case's own analyst opens the review**, and the screen says "Score withheld" instead of showing a number | Seeing the number first anchors the analyst's judgement of each item | `features/analyst/tabs/Summary.tsx` | ADR-45 |
| UX-8 | **The harness is shown, not hidden**: a separate `/monitor/:caseId` window for the analyst, committee and observer shows agents, guards, tokens and the audit chain live; the owner gets 403 on it and on its stream. The observer's only screen is the monitor | The panel needs to see what the AI did. Operational detail is not for the business owner | `features/monitor` | ADR-19, ADR-64 |
| UX-9 | **The committee votes blind**: a member sees their own vote and the count cast, never the others' choice until the round is revealed. A 1-1-1 split shows all three rationales and opens a second round; nothing is decided automatically | Hidden ballots avoid anchoring on the first vote; an automatic defer would decide a case with no majority | `features/committee/tabs/Vote.tsx`, `VoteCard` | ADR-08, ADR-31 |
| UX-10 | **The owner reviews and corrects before submitting.** Review & submit shows every extracted value with its source and lets the owner correct it; the extracted original is kept beside the correction | The owner knows the business; the original shows the analyst what the AI read | `features/po/tabs/ReviewSubmit.tsx`; `field_values.history[]` | D-17 |
| UX-11 | **One batched clarification round before policy review**, drafted by the narrative agent from the completeness check and edited by the analyst; the owner answers in one Inbox | "Being asked the same question twice" is the owners' top complaint (`personas.md`) | analyst Inbox and Summary; owner Inbox | D-16 |
| UX-12 | **The URL is the state**: tabs, filters and the config section live in the path or query, so a reload lands on the same view and any screen can be linked | The demo is driven from links; a reviewer can open the exact screen | design C 1.5 rule 3 | design C |
| UX-13 | **No business arithmetic in the browser**: bands, scores, completion, clocks and tallies come from the server. The prototype computed completion three different ways in the client | One number, one source. `lib/bands.ts` only maps a band number to its label and colour | `src/frontend/CLAUDE.md` | ADR-06 |
| UX-14 | **Server-side configuration drafts with a preview and a publish**, one section at a time, every published version kept | Two analysts see the same draft; a case keeps the versions it was judged under | `features/analyst/config` | ADR-47, ADR-52 |
| UX-15 | **Live updates by stream, with an honest fallback**: one shared event stream per case; when it drops, a banner says "Live updates paused" and the screen polls | The workbench reacts to agents finishing; the user is told when it is not live | `api/sse`, `useStreamStatus` | design C C-03 |
| UX-16 | **Accessibility to WCAG 2.1 AA**: keyboard reach for every action, focus-trapped modals, WAI-ARIA tabs, polite live regions, colour never the only signal (a band badge carries its number and label), reduced motion respected. Body-size text is at least 4.5:1: the warning colour (3.5:1 on white) is used only for large text, icons and borders or on its tinted background, and body-size warning text has its own darker token. The one accepted exception is the brand's primary button, a white label on Virtual Tide at 3.0:1, for button labels only | A compliance tool should not exclude its users. Design C asked for an axe check on every primitive; as built, axe runs in seven component and screen tests and in the live sweep's accessibility pass | `src/frontend/src/test/axe.ts`, `test/design/contrast.test.ts`, `e2e/sweep-live/06-a11y.spec.ts` | design C 1.8, ADR-69 |
| UX-17 | **The Myridius brand guide, light theme only, no hard-coded colours** outside `design/tokens.css`: Montserrat (self-hosted), a linen canvas, Carbon Moss dark surfaces (side navigation, login hero, monitor header), Virtual Tide as the one accent, pill-shaped buttons and inputs, the brand's wave ornament and logo. Token names follow the guide, so a stale class name produces no colour rather than the wrong one | The first tokens came from the prototype and disagreed with the brand guide; the re-skin changed only the look, so every test of behaviour and accessibility still applies. One token file keeps the contrast test meaningful | `src/frontend/src/design/tokens.css`, `components/ui/WaveOrnament.tsx`, `BrandMark.tsx` | ADR-69 (amends ADR-06), D-97 |
| UX-18 | **Out of scope on purpose**: mobile layout, notification settings, a PO "AI configuration" page (it would let an owner tune the agents that assess them) and the prototype's Templates page (now a required-fields card in Review & submit). SharePoint is listed on Integrations as "not connected" | Scope for nine days; the AI configuration page broke the rule that a party cannot tune its own gate | `docs/requirements/source/00-README.md`; teardown section 15 | D-92, ADR-17 |
| UX-19 | **AI-made content is marked**: an animated sparkle sits beside what a model proposed or drafted: extracted values (on the `Extracted` provenance tag), the proposed change types and values found, the completeness report, the clarification draft, the recommendation draft in the workbench and the committee pack, the examiner, the screening agent's fast-lane proposal in the queue, the AI entries in the owner's activity, and the harness views. It stays still for people who ask their system for reduced motion | A person should see at a glance what a model wrote and what a person confirmed, the visual side of "AI proposes, a person decides" | `components/ui/AiMark.tsx` (`AiMark`, `AiLabel`), `components/domain/ProvenanceTag.tsx` | ADR-69 |

## Screens

Captured on 8 October 2026, after the brand re-skin (ADR-69), from the web against its mock backend (`npm run dev:msw`, which serves the P7 seed with no stack and no model calls), at 1440 × 900, using the login page's demo scenarios for case FCRM-2026-0151. The figures are the seed's, not a live run's. Against the mock the monitor shows "reconnecting" because the mock serves no event stream.

**Sign-in (UX-3, UX-17).** The brand's dark hero with the wave ornament and logo, and one way in: corporate SSO, which opens the seeded-user picker in demo mode. The dashed box is the mock backend's scenario switch and never appears against the live stack.

![Sign-in page](img/ux/login.png)

**The owner's case (UX-2, UX-4, UX-6, UX-19).** Priya sees her case's progress in five plain stages, the change types the assistant proposed (marked with the AI sparkle) with the source of each, and her own completion. There is no score, band or rating anywhere on the page, because her responses carry none.

![Owner's case overview](img/ux/po-case-overview.png)

**The analyst's summary (UX-7, UX-11).** The critic's findings and the intake flags, each with its source chip; the completeness report the narrative agent drafted; and "Score withheld" until the analyst has reviewed the proposals.

![Analyst summary with the score withheld](img/ux/analyst-summary.png)

**Policy, word for word (UX-4, UX-6).** Each clause is quoted verbatim with its source, edition, page and corpus version, and is accepted or removed on its own. The banner notes that an attempt which paraphrased a clause was rejected by Guard 4 and retried.

![Policy tab with verbatim clauses](img/ux/analyst-policy.png)

**Risk and controls before review (UX-4, UX-7).** Ratings open only after the clauses are decided. The critic's finding on CTL-T01 sits above the ratings, and the score stays withheld.

![Risk and controls with the score withheld](img/ux/analyst-risk-controls.png)

**The override, with its reason and its consequence (UX-5, UX-13).** Overriding CTL-T01 from the proposed 7.6 to 4.5 shows the scoring engine's preview before anything is saved (composite 9.8 → 10.4, Band 3) and warns that the route changes and needs acknowledgement. "Override with reason" stays disabled until the reason is long enough. The browser computes none of these numbers.

![Override dialog with the scoring preview](img/ux/analyst-override-dialog.png)

**The committee's hidden vote (UX-9).** One of three votes is in and its choice is hidden; a vote needs one of four outcomes and a written rationale.

![Committee vote with hidden ballots](img/ux/committee-vote.png)

**The harness monitor (UX-8).** Agents run, guards passed and blocked, the audit chain, and tokens against the per-case budget, with the last result of each of the seven guards and the step-by-step log. The owner gets 403 on this route.

![Harness monitor](img/ux/harness-monitor.png)

## What the prototype taught, and what was not carried over

The teardown (`docs/requirements/research/04-prototype-fcrm-v2-notes.md`, section 15) lists the prototype's defects. The ones that changed the UX: per-item confirmation and boundary acknowledgement were not enforced (UX-4, and the analyst's Acknowledge action); the committee pack was live rather than frozen (now a hashed, versioned pack); a vote's choice leaked through the API before reveal (UX-9); completion had three client-side formulas (UX-13); the owner could tune the agents that assessed them (UX-18); notifications were never produced (out of scope, not faked).

What was kept: the three-role information architecture and the domain components (`ScoreBadge`, `ProvenanceTag`, `RiskMatrix`, `StageTracker`, `CompletionRing`, the committee vote panel), rebuilt from scratch with tests (ADR-06). The prototype's token layer was kept at first and replaced by the brand guide's on 7 October (ADR-69).

## Where this is checked

- `src/frontend/src/test/mocks/visibility.test.ts` scans every owner response in the mock; `tests/integration/blackbox/test_bb_02_po_no_restricted_fields.py` does the same against the services.
- `src/frontend/src/test/contract/paths.test.ts` fails when the web calls a route the OpenAPI export does not hold.
- Accessibility: axe in seven vitest files (Modal, Button, ChartCard, ReasonDialog, ScoreBadge, the login page and the owner dashboard), the token contrast test, and `e2e/sweep-live/06-a11y.spec.ts` against the running stack.
- Playwright: four mock scenes (`e2e/scenes`: role routing, the analyst queue, the owner dashboard without a score, the committee pack), five live scenes (`e2e/scenes-live`, including the harness monitor) and the live sweep (`e2e/sweep-live`: routes, fences, controls, sign-in, accessibility).
- Open UX gaps are in `docs/governance/known-gaps.md`.
