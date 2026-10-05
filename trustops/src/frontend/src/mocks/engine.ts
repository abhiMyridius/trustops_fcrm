/**
 * Stand-in for S2 (fcrm_scoring) inside the mock backend ONLY, so overrides and simulate previews
 * reproduce the goldens (9.8 Band 2 -> 10.4 Band 3) without a server. The real engine lives in
 * src/services/scoring; this copy is never imported by app code (ESLint fence on @/mocks).
 * Formula per BUILD-PLAN §3.6: CE = Σ w·r over matched non-planned controls; retention =
 * max(0.4, 1 − 0.6·CE/10); residual = inherent × retention; composite = Σ residual × normalised
 * domain weight, rounded once (half-up) to one decimal before band lookup. Boundary flag per ADR-13.
 */
import { seed, type SeedDomain } from './seed';

export interface EngineControl {
  id: string;
  rating: number;
  weight_norm: number;
  planned?: boolean;
}

export interface EngineArea {
  domain: SeedDomain;
  L: number;
  I: number;
  controls: EngineControl[];
}

export interface EngineAreaResult {
  domain: SeedDomain;
  inherent: number;
  ce: number;
  retention: number;
  residual: number;
  weight: number;
  contribution: number;
}

export interface EngineResult {
  areas: EngineAreaResult[];
  composite: number;
  band: 1 | 2 | 3 | 4;
  route: 'analyst' | 'committee';
  boundary_flag: boolean;
  boundary_distance: number | null;
}

const model = seed.scoring_model;

function roundHalfUp(n: number, places: number): number {
  const f = 10 ** places;
  return Math.round((n + Number.EPSILON) * f) / f;
}

export function bandFor(composite: number): (typeof model.bands)[number] {
  const found = model.bands.find((b) => composite >= b.min && composite <= b.max);
  return found ?? model.bands[model.bands.length - 1]!;
}

/** ADR-13: within 0.5 of the next band's lower edge, or of the edge just crossed (this band's lower edge). */
export function boundary(composite: number): { flag: boolean; distance: number | null } {
  const b = bandFor(composite);
  const next = model.bands.find((x) => x.band === b.band + 1);
  const distances: number[] = [];
  if (next) distances.push(roundHalfUp(next.min - composite, 1));
  if (b.band > 1) distances.push(roundHalfUp(composite - b.min, 1));
  const near = distances.filter((d) => d >= 0 && d <= model.boundary_flag);
  if (near.length === 0) return { flag: false, distance: null };
  return { flag: true, distance: Math.min(...near) };
}

export function score(areas: EngineArea[]): EngineResult {
  const weightSum = areas.reduce((s, a) => s + model.domain_weights[a.domain], 0);
  const results = areas.map((a): EngineAreaResult => {
    const scored = a.controls.filter((c) => !c.planned);
    const ce = scored.reduce((s, c) => s + c.weight_norm * c.rating, 0);
    const retention = Math.max(model.retention_floor, 1 - (model.retention_factor * ce) / 10);
    const inherent = a.L * a.I;
    const residual = inherent * retention;
    const weight = model.domain_weights[a.domain] / weightSum;
    return { domain: a.domain, inherent, ce, retention, residual, weight, contribution: residual * weight };
  });
  const composite = roundHalfUp(
    results.reduce((s, r) => s + r.contribution, 0),
    1,
  );
  const b = bandFor(composite);
  const bd = boundary(composite);
  return {
    areas: results.map((r) => ({
      ...r,
      ce: roundHalfUp(r.ce, 2),
      retention: roundHalfUp(r.retention, 2),
      residual: roundHalfUp(r.residual, 1),
      weight: roundHalfUp(r.weight, 2),
      contribution: roundHalfUp(r.contribution, 2),
    })),
    composite,
    band: b.band,
    route: b.route,
    boundary_flag: bd.flag,
    boundary_distance: bd.distance,
  };
}

export function bandRange(band: number): string {
  const b = model.bands.find((x) => x.band === band);
  return b ? `${b.min.toFixed(1)}–${b.max.toFixed(1)}` : '';
}
