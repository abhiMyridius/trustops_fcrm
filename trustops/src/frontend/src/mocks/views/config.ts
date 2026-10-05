/**
 * The eight configuration sections (design C §2.24) built from the seed. Each section's version
 * history is the seed's active version plus any version published in this session; drafts are
 * server-side with an etag so two analysts see the same working set.
 */
import type { ChangeType, Domain } from '@/api/types/common';
import type { ConfigSection, ConfigSectionKey, ConfigVersion } from '@/api/types/config';
import { getDB, STORY_TODAY, type ConfigState } from '../db';
import { seed, type SeedDomain } from '../seed';

export const SECTION_KEYS: ConfigSectionKey[] = [
  'field-sets',
  'categories',
  'scoring-model',
  'control-inventory',
  'policy-corpus',
  'routing-rules',
  'jurisdiction-risk',
  'ai-agents',
];

const META: Record<ConfigSectionKey, { title: string; version: () => string; published: string; owner: string }> = {
  'field-sets': { title: 'Categories & Fields', version: () => seed.field_set.version, published: seed.meta.generated ?? '2026-10-01', owner: 'FCRM Analytics' },
  categories: { title: 'Classification Rules', version: () => 'v1', published: seed.meta.generated ?? '2026-10-01', owner: 'FCRM Analytics' },
  'scoring-model': { title: 'Scoring Model', version: () => seed.scoring_model.version, published: seed.scoring_model.approved, owner: seed.scoring_model.owner },
  'control-inventory': { title: 'Control Inventory', version: () => seed.meta.controls ?? 'v9', published: seed.meta.generated ?? '2026-10-01', owner: 'FCRM Controls' },
  'policy-corpus': { title: 'Policy Corpus', version: () => seed.corpus.version, published: seed.corpus.ingested, owner: 'FCRM Policy' },
  'routing-rules': { title: 'Rules & Routing', version: () => seed.routing.version, published: seed.meta.generated ?? '2026-10-01', owner: 'FCRM Analytics' },
  'jurisdiction-risk': { title: 'Jurisdiction Risk List', version: () => seed.jurisdictions.current.version, published: seed.jurisdictions.current.published, owner: 'FCRM Sanctions' },
  'ai-agents': { title: 'AI Agents', version: () => seed.meta.prompts ?? '2026-09-28', published: seed.meta.prompts ?? '2026-09-28', owner: 'FCRM AI governance' },
};

function state(section: ConfigSectionKey): ConfigState {
  const db = getDB();
  return (db.config[section] ??= { extraVersions: [], draft: [], etag: 1 });
}

function versions(section: ConfigSectionKey): ConfigVersion[] {
  const m = META[section];
  const st = state(section);
  const list: ConfigVersion[] = [
    { version: m.version(), published_at: m.published, by: m.owner, note: 'Seeded version (P7 fcrm-seed)', active: st.extraVersions.length === 0 },
    ...st.extraVersions.map((v, i) => ({ ...v, active: i === st.extraVersions.length - 1 })),
  ];
  return list.reverse();
}

function monthsSince(iso: string): number {
  const a = new Date(`${iso}T00:00:00Z`);
  const b = new Date(`${STORY_TODAY}T00:00:00Z`);
  return (b.getUTCFullYear() - a.getUTCFullYear()) * 12 + (b.getUTCMonth() - a.getUTCMonth());
}

function data(section: ConfigSectionKey): ConfigSection['data'] {
  switch (section) {
    case 'field-sets':
      return {
        max_required: seed.field_set.max_required,
        completion_threshold: seed.field_set.completion_threshold,
        always_required: seed.field_set.always_required.map((f) => ({ id: f.id, label: f.label, note: f.note ?? null })),
        per_type: Object.entries(seed.field_set.per_type).map(([type, fields]) => ({ type: type as ChangeType, label: seed.change_types[type] ?? type, fields })),
      };
    case 'categories':
      return {
        types: Object.entries(seed.change_types).map(([type, label]) => ({
          type: type as ChangeType,
          label,
          domains: (seed.type_domains[type] ?? []) as Domain[],
          signals: SIGNALS[type] ?? [],
        })),
        fast_lane: {
          volume_under_usd: 5_000_000,
          conditions: ['Only change type is a vendor renewal, or a feature', 'No new geography, segment or product', 'Annual volume under $5M', 'Screening agent proposes; analyst confirms (R4)'],
        },
      };
    case 'scoring-model':
      return {
        domain_weights: (Object.entries(seed.scoring_model.domain_weights) as [SeedDomain, number][]).map(([domain, weight]) => ({ domain: domain as Domain, label: seed.domains[domain], weight })),
        likelihood_scale: Object.entries(seed.scoring_model.likelihood_scale).map(([v, label]) => ({ value: Number(v), label })),
        impact_scale: Object.entries(seed.scoring_model.impact_scale).map(([v, label]) => ({ value: Number(v), label })),
        bands: seed.scoring_model.bands,
        retention_factor: seed.scoring_model.retention_factor,
        retention_floor: seed.scoring_model.retention_floor,
        boundary_flag: seed.scoring_model.boundary_flag,
        rounding: seed.scoring_model.rounding,
        approved: seed.scoring_model.approved,
        owner: seed.scoring_model.owner,
      };
    case 'control-inventory': {
      const sums = new Map<SeedDomain, number>();
      for (const c of seed.controls) sums.set(c.domain, (sums.get(c.domain) ?? 0) + c.weight);
      return {
        controls: seed.controls.map((c) => ({ ...c, domain: c.domain as Domain, stale: monthsSince(c.last_tested) > 18 })),
        domain_weight_sums: [...sums.entries()].map(([domain, sum]) => ({ domain: domain as Domain, sum: Math.round(sum * 100) / 100 })),
      };
    }
    case 'policy-corpus':
      return { clause_count: seed.corpus.clause_count, ingested: seed.corpus.ingested, sources: seed.corpus.sources };
    case 'routing-rules':
      return {
        rules: seed.routing.rules,
        parameters: [
          { key: 'boundary', label: 'Boundary flag distance', value: String(seed.scoring_model.boundary_flag) },
          { key: 'recency', label: 'Evidence recency (stale after)', value: '18 months' },
          { key: 'nearing', label: 'Nearing-due reminder', value: '3 days' },
          { key: 'fast_lane_volume', label: 'Fast-lane annual volume', value: 'under $5M' },
          { key: 'quorum', label: 'Committee quorum', value: '3' },
          { key: 'reminder', label: 'Condition reminder', value: '14 days before due' },
        ],
      };
    case 'jurisdiction-risk':
      return {
        current: {
          version: seed.jurisdictions.current.version,
          published: seed.jurisdictions.current.published,
          source: seed.jurisdictions.current.source,
          call_for_action: seed.jurisdictions.current.call_for_action,
          increased_monitoring: seed.jurisdictions.current.increased_monitoring,
          added: seed.jurisdictions.current.changes.added,
          removed: seed.jurisdictions.current.changes.removed,
        },
        fixture: { version: seed.jurisdictions.fixture.version, note: seed.jurisdictions.fixture.note, added: seed.jurisdictions.fixture.added },
        unlisted_default: 'Unlisted jurisdictions default to "standard" and are shown as such, never silently.',
      };
    case 'ai-agents':
      return {
        agents: seed.agents.map((a) => {
          const budget = seed.budgets[a.tier];
          const ev = seed.evals.per_agent[a.name];
          return {
            ...a,
            budget: typeof budget === 'object' ? `${(budget.tokens_per_run / 1000).toFixed(0)}k tokens · ${budget.wall_s}s · ${budget.retries} retry` : '',
            eval_score: ev?.pct ?? 0,
            eval_metric: ev?.metric ?? '',
          };
        }),
        per_case_budget: typeof seed.budgets.per_case === 'number' ? seed.budgets.per_case : 120000,
      };
  }
}

/** Example classification signals: the seed carries no signal table yet, so these are illustrative and labelled so in the UI. */
const SIGNALS: Record<string, string[]> = {
  new_product: ['new product name or SKU', 'pricing or limits for a new offering', 'product sign-off reference'],
  new_feature: ['change to an existing product', 'feature flag or release note', 'no new segment or geography'],
  process_change: ['procedure or SOP change', 'control owner change', 'automation of a manual step'],
  vendor_onboarding: ['named third party or payout partner', 'contract or MSA', 'outsourced service'],
  new_geography: ['new country or corridor', 'new currency', 'local licensing'],
  new_segment: ['new customer type', 'onboarding standard for a new group', 'segment definition'],
};

export function configSection(section: ConfigSectionKey): ConfigSection {
  const m = META[section];
  const st = state(section);
  const active = versions(section).find((v) => v.active)?.version ?? m.version();
  return {
    section,
    title: m.title,
    owner: m.owner,
    active_version: active,
    versions: versions(section),
    draft: { etag: `W/"${section}-${st.etag}"`, changes: st.draft },
    data: data(section),
  } as ConfigSection;
}

export function etagOf(section: ConfigSectionKey): string {
  return `W/"${section}-${state(section).etag}"`;
}

export function saveDraft(section: ConfigSectionKey, change: { path: string; before: string; after: string }): void {
  const st = state(section);
  st.draft = [...st.draft.filter((c) => c.path !== change.path), change];
  st.etag += 1;
}

export function discardDraft(section: ConfigSectionKey): void {
  const st = state(section);
  st.draft = [];
  st.etag += 1;
}

export function openCasesPinned(): number {
  return seed.cases.filter((c) => c.stage !== 'closed').length;
}

export function publish(section: ConfigSectionKey, note: string, by: string): void {
  const st = state(section);
  const current = versions(section).find((v) => v.active)?.version ?? 'v1';
  const m = /^v(\d+)$/.exec(current);
  const next = m ? `v${Number(m[1]) + 1}` : `${current}.1`;
  st.extraVersions.push({ version: next, published_at: STORY_TODAY, by, note });
  st.draft = [];
  st.etag += 1;
}
