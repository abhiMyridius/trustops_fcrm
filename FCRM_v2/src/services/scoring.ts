// Deterministic scoring engine (§3). Pure functions only - the AI proposes
// factor inputs and control-effectiveness values; this module is the only
// thing that ever computes a score. No inference here, only arithmetic.

import type { AmlCategory, BandDef, Band, BoundaryFlag, CaseControl, CategoryScore, RoutingRules, ScoringModelVersion, ScoringResult } from '@/types/analyst';

// Full precision - never round this before using it in residualScore, or the
// composite math drifts off the worked examples in §7.4 / §7.6.
export function retentionFactorRaw(controlEffectiveness: number, floorPct: number): number {
  const floor = floorPct / 100; // 0.4
  return 1 - (1 - floor) * (controlEffectiveness / 10);
}

/** Rounded, for display only. */
export function retentionFactor(controlEffectiveness: number, floorPct: number): number {
  return round(retentionFactorRaw(controlEffectiveness, floorPct), 2);
}

export function inherentScore(likelihood: number, impact: number): number {
  return likelihood * impact;
}

export function residualScore(inherent: number, controlEffectiveness: number, floorPct: number): number {
  return round(inherent * retentionFactorRaw(controlEffectiveness, floorPct), 1);
}

export function controlEffectivenessForCategory(controls: CaseControl[], category: AmlCategory): number {
  // Proposed controls count toward scoring using the AI-proposed effectiveness value
  // (via effectivenessConfirmed ?? effectivenessAI below) - the score is available
  // immediately after the analysis run, before the analyst confirms or overrides anything.
  const inCategory = controls.filter((c) => c.category === category);
  if (inCategory.length === 0) return 0;
  const totalWeight = inCategory.reduce((s, c) => s + c.weight, 0);
  if (totalWeight === 0) return 0;
  const weighted = inCategory.reduce((s, c) => s + (c.effectivenessConfirmed ?? c.effectivenessAI) * c.weight, 0);
  return round(weighted / totalWeight, 1);
}

export function bandFor(compositeResidual: number, bands: BandDef[]): BandDef {
  return bands.find((b) => compositeResidual >= b.min && compositeResidual <= b.max) ?? bands[bands.length - 1];
}

// Boundary convention (§8.4 mod, §10.4): distance is measured to the START of the neighboring
// band, not to the edge of the current band. Band ranges are not contiguous (Band 2 ends at
// 10.0, Band 3 begins at 10.1) so those two measures disagree by the gap between them, and only
// one is decision-relevant - how far until the composite actually reclassifies into that band.
export function boundaryProximity(compositeResidual: number, bands: BandDef[], threshold: number): BoundaryFlag | null {
  const current = bandFor(compositeResidual, bands);
  if (current.band < 4) {
    const next = bands.find((b) => b.band === current.band + 1)!;
    const distance = round(next.min - compositeResidual, 2);
    if (distance <= threshold) {
      return { distance, currentBand: current.band, otherBand: next.band, otherRoute: next.route, direction: 'above' };
    }
  }
  if (current.band > 1) {
    const prev = bands.find((b) => b.band === current.band - 1)!;
    const distance = round(compositeResidual - prev.max, 2);
    if (distance <= threshold) {
      return { distance, currentBand: current.band, otherBand: prev.band, otherRoute: prev.route, direction: 'below' };
    }
  }
  return null;
}

function round(n: number, decimals: number): number {
  const f = 10 ** decimals;
  return Math.round(n * f) / f;
}

export interface CategoryFactorInput {
  category: AmlCategory;
  likelihood: number;
  impact: number;
  likelihoodProvenance: CategoryScore['likelihoodProvenance'];
  impactProvenance: CategoryScore['impactProvenance'];
  likelihoodBaselineDetail?: string;
  likelihoodOverrideReason?: string;
  impactOverrideReason?: string;
  likelihoodProposed?: number;
  likelihoodProposedProvenance?: CategoryScore['likelihoodProvenance'];
  impactProposed?: number;
  impactProposedProvenance?: CategoryScore['impactProvenance'];
}

/** Computes the full scoring result for a case. This is the single source of truth for every number shown. */
export function computeScoring(
  model: ScoringModelVersion,
  inScopeCategories: AmlCategory[],
  factorInputs: CategoryFactorInput[],
  controls: CaseControl[],
  routing: RoutingRules,
): ScoringResult {
  const rawWeights = inScopeCategories.map((c) => model.categoryWeights[c] ?? 0);
  const weightSum = rawWeights.reduce((s, w) => s + w, 0) || 1;

  const categories: CategoryScore[] = inScopeCategories.map((category, i) => {
    const factor = factorInputs.find((f) => f.category === category);
    const likelihood = factor?.likelihood ?? 1;
    const impact = factor?.impact ?? 1;
    const inherent = inherentScore(likelihood, impact);
    const ce = controlEffectivenessForCategory(controls, category);
    const retention = retentionFactor(ce, model.retentionFloorPct);
    const residual = residualScore(inherent, ce, model.retentionFloorPct);
    const weight = round(rawWeights[i] / weightSum, 2);
    return {
      category,
      likelihood,
      impact,
      likelihoodProvenance: factor?.likelihoodProvenance ?? 'submission',
      impactProvenance: factor?.impactProvenance ?? 'submission',
      inherent,
      controlEffectiveness: ce,
      retention,
      residual,
      weight,
      likelihoodBaselineDetail: factor?.likelihoodBaselineDetail,
      likelihoodOverrideReason: factor?.likelihoodOverrideReason,
      impactOverrideReason: factor?.impactOverrideReason,
      likelihoodProposed: factor?.likelihoodProposed,
      likelihoodProposedProvenance: factor?.likelihoodProposedProvenance,
      impactProposed: factor?.impactProposed,
      impactProposedProvenance: factor?.impactProposedProvenance,
    };
  });

  const compositeInherent = round(
    categories.reduce((s, c) => s + c.inherent * c.weight, 0),
    1,
  );
  const compositeResidual = round(
    categories.reduce((s, c) => s + c.residual * c.weight, 0),
    1,
  );
  const bandDef = bandFor(compositeResidual, model.bands);
  const boundaryFlag = boundaryProximity(compositeResidual, model.bands, routing.boundaryProximityThreshold);

  return {
    modelVersion: model.version,
    categories,
    compositeInherent,
    compositeResidual,
    band: bandDef.band,
    route: bandDef.route,
    boundaryFlag,
    boundaryAcknowledged: false,
    computedAt: new Date().toISOString(),
  };
}

export function bandChanged(prev: ScoringResult | null, next: ScoringResult): boolean {
  return !!prev && prev.band !== next.band;
}
