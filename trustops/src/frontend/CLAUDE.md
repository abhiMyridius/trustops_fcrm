# src/frontend (W1) — package guidance

React 19 + TypeScript (strict, `noUncheckedIndexedAccess`, `verbatimModuleSyntax`) + Vite 8 + Tailwind v4 + TanStack Query 5 + React Router 7 + MSW 2. Rebuilt from scratch (ADR-06); FCRM_v2 contributed tokens and IA only.

```bash
export PATH="$HOME/.local/node22/bin:$PATH"
npm run dev        # :5173, live: Vite proxies /api to the gateway on :8080 (the stack must be up)
npm run dev:msw    # :5173, VITE_API_MODE=msw: the in-browser mock backend from the P7 seed, no stack needed
npm run typecheck  # tsc -b --noEmit
npm run lint       # oxlint (correctness + jsx-a11y) then eslint (jsx-a11y, hooks, import fences)
npm run test       # vitest + Testing Library + MSW node + vitest-axe; specs live in src/test/** only
npm run build      # tsc -b && vite build
npm run test:e2e       # Playwright scenes in e2e/scenes against `dev:msw`
npm run test:e2e:live  # Playwright against the gateway on :8080 (e2e/playwright.live.config.ts; five scenes in e2e/scenes-live/)
```

Live is the default and the only mode a production build has: anything but an explicit `VITE_API_MODE=msw` is live, and the Dockerfile deletes `mockServiceWorker.js` from the image. MSW is the vitest backend and the `dev:msw` backend; the web's route names are the API contract and the backend adopts them.

Rules that the tooling enforces:
- `features/po/**` cannot import score-bearing components, analyst/committee DTOs or their queries (ESLint `no-restricted-imports`). PO DTOs in `api/types/po.ts` have no score/band/rating/clause/vote fields; `src/test/mocks/visibility.test.ts` scans every PO response.
- App code never imports `@/mocks/*`; only `main.tsx` (MSW mode) and `src/test` do.
- No business arithmetic in the client: bands, scores, completion, clocks and tallies come from the server. `lib/bands.ts` maps a band number to label/tone only.
- Lists: every list route answers `Page{items,next_cursor,total}` and the list hooks read it with `apiList` (`api/client.ts`); the MSW handlers answer the same shape (`page()` in `mocks/http.ts`).
- Live updates: `useCaseStream(caseId)` (opened by the PO case frame, the workbench frame and the monitor) holds one shared `EventSource` per case, sends the access token as `?access_token=`, refreshes the session before every reopen and maps backend event names to query keys (`api/sse/eventSchemas.ts`). Screens read `useStreamStatus(caseId)` and poll only when it is not `live`.
- Contract ratchet: `src/test/contract/paths.test.ts` checks every route the web calls (`METHOD /path`) against `docs/architecture/openapi/api.json` (`make openapi`) or its `PENDING_ROUTES` list; it fails when a listed route becomes served, so the lane that adds a route deletes its entry and regenerates the OpenAPI.
- Every HTTP path lives in `api/endpoints.ts`; the MSW handlers use the same builders. When `make api-gen` produces `api/generated/*.d.ts`, swap the hand-written `api/types/*` for the generated types; call sites stay.
- No hex or rgba colours outside `design/tokens.css` (and `public/*.svg`). The font is self-hosted: `@fontsource-variable/montserrat`, imported in `main.tsx`, never a Google Fonts `<link>`.
- `oxlint` rules turned off on purpose: `react/no-unstable-nested-components` (table `cell` render callbacks are not components) and `jsx-a11y/prefer-tag-over-role` (role=dialog/status/progressbar/img follow the WAI-ARIA patterns; native `<dialog>` is not supported by jsdom).

Mock backend (`src/mocks`): FCRM-2026-0151 is a live state machine (`actions.ts`) that appends the seed's audit records in story order, so a full click-through ends at 67 records; the other 79 cases are read-only seed projections. The login page's scenario switch (MSW only) resets to scene 1 / 8 / 17 / 19.
