# src/frontend (W1) — package guidance

React 19 + TypeScript (strict, `noUncheckedIndexedAccess`, `verbatimModuleSyntax`) + Vite 8 + Tailwind v4 + TanStack Query 5 + React Router 7 + MSW 2. Rebuilt from scratch (ADR-06); FCRM_v2 contributed tokens and IA only.

```bash
export PATH="$HOME/.local/node22/bin:$PATH"
npm run dev        # :5173, VITE_API_MODE=msw by default (mock backend from the P7 seed)
npm run typecheck  # tsc -b --noEmit
npm run lint       # oxlint (correctness + jsx-a11y) then eslint (jsx-a11y, hooks, import fences)
npm run test       # vitest + Testing Library + MSW node + vitest-axe; specs live in src/test/** only
npm run build      # tsc -b && vite build
```

Rules that the tooling enforces:
- `features/po/**` cannot import score-bearing components, analyst/committee DTOs or their queries (ESLint `no-restricted-imports`). PO DTOs in `api/types/po.ts` have no score/band/rating/clause/vote fields; `src/test/mocks/visibility.test.ts` scans every PO response.
- App code never imports `@/mocks/*`; only `main.tsx` (MSW mode) and `src/test` do.
- No business arithmetic in the client: bands, scores, completion, clocks and tallies come from the server. `lib/bands.ts` maps a band number to label/tone only.
- Every HTTP path lives in `api/endpoints.ts`; the MSW handlers use the same builders. When `make api-gen` produces `api/generated/*.d.ts`, swap the hand-written `api/types/*` for the generated types; call sites stay.
- No hex colours outside `design/tokens.css`.
- `oxlint` rules turned off on purpose: `react/no-unstable-nested-components` (table `cell` render callbacks are not components) and `jsx-a11y/prefer-tag-over-role` (role=dialog/status/progressbar/img follow the WAI-ARIA patterns; native `<dialog>` is not supported by jsdom).

Mock backend (`src/mocks`): FCRM-2026-0151 is a live state machine (`actions.ts`) that appends the seed's audit records in story order, so a full click-through ends at 67 records; the other 79 cases are read-only seed projections. The login page's scenario switch (MSW only) resets to scene 1 / 8 / 17 / 19.
