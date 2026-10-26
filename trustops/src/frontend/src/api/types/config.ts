/** Configuration DTOs (S3 policy config, S2 scoring/routing). Analyst only (design C §2.24). */
import type { ChangeType, Domain } from './common';
import type { BandDef } from './analyst';

export type ConfigSectionKey =
  | 'field-sets'
  | 'categories'
  | 'scoring-model'
  | 'control-inventory'
  | 'policy-corpus'
  | 'routing-rules'
  | 'jurisdiction-risk'
  | 'ai-agents';

export interface ConfigVersion {
  version: string;
  published_at: string;
  by: string;
  note: string;
  active: boolean;
}

export interface ConfigDraft {
  etag: string;
  changes: { path: string; before: string; after: string }[];
}

export interface ConfigSectionBase<K extends ConfigSectionKey, D> {
  section: K;
  title: string;
  owner: string;
  active_version: string;
  versions: ConfigVersion[];
  draft: ConfigDraft;
  data: D;
}

export interface FieldSetData {
  max_required: number;
  completion_threshold: number;
  always_required: { id: string; label: string; note: string | null }[];
  per_type: { type: ChangeType; label: string; fields: { id: string; label: string }[] }[];
  /** Each change type's FFIEC AML risk categories: the risk areas a case is assessed in. Read-only. */
  aml_mapping: { type: ChangeType; label: string; categories: { domain: Domain; label: string }[] }[];
}

export interface CategoriesData {
  types: { type: ChangeType; label: string; domains: Domain[]; signals: string[] }[];
  fast_lane: { volume_under_usd: number; conditions: string[] };
}

export interface ScoringModelData {
  domain_weights: { domain: Domain; label: string; weight: number }[];
  likelihood_scale: { value: number; label: string }[];
  impact_scale: { value: number; label: string }[];
  bands: BandDef[];
  retention_factor: number;
  retention_floor: number;
  boundary_flag: number;
  rounding: string;
  approved: string;
  owner: string;
}

export interface ControlInventoryData {
  controls: {
    id: string;
    domain: Domain;
    name: string;
    weight: number;
    evidence_artefact: string;
    last_tested: string;
    stale: boolean;
  }[];
  domain_weight_sums: { domain: Domain; sum: number }[];
}

export interface PolicyCorpusData {
  clause_count: number;
  ingested: string;
  sources: { id: string; title: string; kind: string; sections: string[]; used_for: string; clauses: number }[];
}

export interface RoutingRulesData {
  rules: { id: string; rule: string }[];
  /** `editable` is false for a value shown but set elsewhere (the boundary, the case SLA). */
  parameters: { key: string; label: string; value: string; editable: boolean }[];
}

export interface JurisdictionData {
  current: {
    version: string;
    published: string;
    source: string;
    call_for_action: string[];
    increased_monitoring: string[];
    added: string[];
    removed: string[];
  };
  fixture: { version: string; note: string; added: string[] };
  unlisted_default: string;
  /** The 1-5 scale: each level's label and what it means. */
  anchors: { level: number; label: string; definition: string }[];
  /** One row per jurisdiction, strictest first; an internal row is edited at `path` (a draft). */
  entries: JurisdictionEntry[];
}

export interface JurisdictionEntry {
  id: string;
  jurisdiction: string;
  rating: number;
  rating_label: string;
  source: string;
  publisher: string;
  source_type: 'external' | 'internal';
  editable: boolean;
  path: string | null;
  note: string | null;
}

export interface AgentsData {
  agents: {
    name: string;
    tier: string;
    model: string;
    fallback: string;
    tools: string[];
    skills: string[];
    output: string;
    gate: string;
    eval_set: string;
    budget: string;
    eval_score: number;
    eval_metric: string;
  }[];
  per_case_budget: number;
}

export type ConfigSection =
  | ConfigSectionBase<'field-sets', FieldSetData>
  | ConfigSectionBase<'categories', CategoriesData>
  | ConfigSectionBase<'scoring-model', ScoringModelData>
  | ConfigSectionBase<'control-inventory', ControlInventoryData>
  | ConfigSectionBase<'policy-corpus', PolicyCorpusData>
  | ConfigSectionBase<'routing-rules', RoutingRulesData>
  | ConfigSectionBase<'jurisdiction-risk', JurisdictionData>
  | ConfigSectionBase<'ai-agents', AgentsData>;

export interface ImpactPreview {
  section: ConfigSectionKey;
  open_cases_pinned: number;
  summary: string;
  non_retroactive: boolean;
}

export interface ExaminerAnswer {
  question: string;
  case_id: string;
  refused: boolean;
  sentences: { t: string; refs: number[] }[];
  references: string[];
}

export interface OverrideRow {
  case_id: string;
  item: string;
  proposed: number;
  final: number;
  by: string;
  reason: string;
  agent: string;
  date: string;
}

export interface AgentQualityRow {
  agent: string;
  metric: string;
  n: number;
  pct: number;
  lower_is_better: boolean;
}

export interface AgentQuality {
  week: string;
  golden_cases: number;
  regressions: number;
  drift: string;
  rows: AgentQualityRow[];
}

export interface VersionsRow {
  item: string;
  version: string;
}
