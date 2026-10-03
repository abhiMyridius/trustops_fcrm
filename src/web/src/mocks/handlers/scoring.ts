/** S2 scoring stand-in: model and routing versions, and the simulate preview used by ReasonDialog. */
import { HttpResponse, http } from 'msw';
import { API_PREFIX, paths } from '@/api/endpoints';
import type { SimulateResponse } from '@/api/types/analyst';
import { demoAreas, demoScore } from '../actions';
import { score } from '../engine';
import { body, guarded, notFound, problem } from '../http';
import { seed } from '../seed';
import { isDemo } from '../views/common';

export const scoringHandlers = [
  http.get(`${API_PREFIX}/scoring/models/:v`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst', 'fcrm_committee'], () =>
      params.v === seed.scoring_model.version ? HttpResponse.json(seed.scoring_model) : notFound(),
    ),
  ),
  http.get(`${API_PREFIX}/scoring/routing-rules/:v`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst', 'fcrm_committee'], () => (params.v === seed.routing.version ? HttpResponse.json(seed.routing) : notFound())),
  ),
  http.post(paths.scoring.simulate, ({ request }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const b = await body<{ case_id: string; control_id: string; value: number }>(request);
      if (!isDemo(b.case_id)) return problem(409, 'Read-only', 'Simulation runs on the live case only in mock mode.');
      if (!Number.isFinite(b.value) || b.value < 0 || b.value > 10) return problem(422, 'Invalid value', 'A rating is between 0 and 10.');
      const areas = demoAreas();
      const area = areas.find((a) => a.controls.some((c) => c.id === b.control_id));
      if (!area) return notFound();
      const before = demoScore();
      const after = score(
        areas.map((a) => (a === area ? { ...a, controls: a.controls.map((c) => (c.id === b.control_id ? { ...c, rating: b.value } : c)) } : a)),
      );
      const ba = before.areas.find((x) => x.domain === area.domain)!;
      const aa = after.areas.find((x) => x.domain === area.domain)!;
      const res: SimulateResponse = {
        domain: area.domain,
        ce: [ba.ce, aa.ce],
        retention: [ba.retention, aa.retention],
        residual: [ba.residual, aa.residual],
        composite: [before.composite, after.composite],
        band: [before.band, after.band],
        line: `CE ${ba.ce.toFixed(1)} → ${aa.ce.toFixed(1)} · retention ${ba.retention.toFixed(2)} → ${aa.retention.toFixed(2)} · residual ${ba.residual.toFixed(1)} → ${aa.residual.toFixed(1)} · composite ${before.composite.toFixed(1)} → ${after.composite.toFixed(1)} · Band ${after.band}`,
      };
      return HttpResponse.json(res);
    }),
  ),
];
