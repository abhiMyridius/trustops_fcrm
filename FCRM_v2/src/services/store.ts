import type { AgentConfig, CaseDetail, FieldSetSnapshot, Integration, Notification, NotificationPreferences, RequiredFieldDef, Template, TriggerCategory, TriggerCategoryDef } from '@/types';
import { ALL_CATEGORIES } from '@/types';
import type {
  AnalystAgentConfig,
  AnalystAgentConfigSnapshot,
  AnalystCaseData,
  ClassificationRulesSnapshot,
  ControlInventorySnapshot,
  JurisdictionRiskEntry,
  JurisdictionRiskListSnapshot,
  PolicyCorpusSnapshot,
  RoutingRules,
  RoutingRulesSnapshot,
  ScoringModelVersion,
} from '@/types/analyst';
import { TRIGGER_TO_AML } from '@/types/analyst';
import { SEED_CASES } from '@/mocks/seed/cases';
import { CATEGORY_DESCRIPTIONS, TEMPLATES } from '@/mocks/seed/templates';
import { INTEGRATIONS } from '@/mocks/seed/integrations';
import { DEFAULT_AGENT_CONFIGS } from '@/mocks/seed/models';
import { DEFAULT_NOTIFICATION_EVENTS, SEED_NOTIFICATIONS } from '@/mocks/seed/notifications';
import { CATEGORY_PACK_KEYS, CORE_SET_KEYS, FIELD_LIBRARY, GLOBAL_COMPLETION_THRESHOLD } from '@/mocks/seed/requiredFields';
import { SEED_ANALYST_CASES } from '@/mocks/seed/analystCases';
import {
  ANALYST_AGENT_CONFIG_VERSION_META,
  CLASSIFICATION_RULES_VERSION_META,
  CLASSIFICATION_SIGNALS,
  CONTROL_INVENTORY,
  CONTROL_INVENTORY_VERSION_META,
  FIELD_SET_VERSION_THRESHOLDS,
  FIELD_SET_VERSIONS,
  JURISDICTION_RISK_LIST_VERSION_META,
  POLICY_CORPUS,
  POLICY_CORPUS_VERSION_META,
  POLICY_SOURCES,
  ROUTING_RULES_VERSION_META,
  SCORING_MODEL_VERSIONS,
  SCREENING_AUTO_CLEAR_RULES,
  TRIGGER_PROPOSAL_THRESHOLDS,
} from '@/mocks/seed/analystConfig';

// In-memory store - the mock backing "database" for the whole prototype.
// Not persisted across reload by design (§3.1).

function deepClone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v));
}

class Store {
  cases: CaseDetail[] = deepClone(SEED_CASES);
  templates: Record<TriggerCategory, Template> = deepClone(TEMPLATES);
  integrations: Integration[] = deepClone(INTEGRATIONS);
  agentConfigs: AgentConfig[] = deepClone(DEFAULT_AGENT_CONFIGS);
  notifications: Notification[] = deepClone(SEED_NOTIFICATIONS);
  // Email is always available and defaults on; every other channel starts absent since every
  // integration starts disconnected (§8.9 mod, §1).
  notificationPrefs: NotificationPreferences = {
    channels: ['email'],
    events: deepClone(DEFAULT_NOTIFICATION_EVENTS),
  };

  // -------------------- Analyst persona --------------------
  analystCases: Record<string, AnalystCaseData> = deepClone(SEED_ANALYST_CASES);
  scoringModelVersions: ScoringModelVersion[] = deepClone(SCORING_MODEL_VERSIONS);

  // Control Inventory (§10.4) - versioned the same way as every other Config page.
  controlInventorySnapshots: ControlInventorySnapshot[] = CONTROL_INVENTORY_VERSION_META.map((v) => ({ ...v, controls: deepClone(CONTROL_INVENTORY) }));

  activeControlInventory(): ControlInventorySnapshot {
    return this.controlInventorySnapshots.at(-1)!;
  }

  controlInventoryForVersion(version?: number): ControlInventorySnapshot {
    return this.controlInventorySnapshots.find((s) => s.version === version) ?? this.activeControlInventory();
  }

  // Policy Corpus (§10.5) - versioned the same way as every other Config page.
  policyCorpusSnapshots: PolicyCorpusSnapshot[] = POLICY_CORPUS_VERSION_META.map((v) => ({ ...v, sources: deepClone(POLICY_SOURCES), clauses: deepClone(POLICY_CORPUS) }));

  activePolicyCorpus(): PolicyCorpusSnapshot {
    return this.policyCorpusSnapshots.at(-1)!;
  }

  policyCorpusForVersion(version?: number): PolicyCorpusSnapshot {
    return this.policyCorpusSnapshots.find((s) => s.version === version) ?? this.activePolicyCorpus();
  }

  // Jurisdiction Risk List (§10.7) - versioned the same way as every other Config section.
  jurisdictionRiskListSnapshots: JurisdictionRiskListSnapshot[] = deepClone(JURISDICTION_RISK_LIST_VERSION_META);

  activeJurisdictionRiskList(): JurisdictionRiskListSnapshot {
    return this.jurisdictionRiskListSnapshots.at(-1)!;
  }

  jurisdictionRiskListForVersion(version?: number): JurisdictionRiskListSnapshot {
    return this.jurisdictionRiskListSnapshots.find((s) => s.version === version) ?? this.activeJurisdictionRiskList();
  }

  /** Convenience read of the active version's entries - every existing call site that reads
   * `store.jurisdictionRiskList` keeps working unchanged against whatever is current. */
  get jurisdictionRiskList(): JurisdictionRiskEntry[] {
    return this.activeJurisdictionRiskList().entries;
  }

  // AI Agents (§10.8) - versioned the same way as every other Config section. Team-global by
  // design: one version applies to every analyst, not per-analyst.
  analystAgentConfigSnapshots: AnalystAgentConfigSnapshot[] = deepClone(ANALYST_AGENT_CONFIG_VERSION_META);

  activeAnalystAgentConfigs(): AnalystAgentConfigSnapshot {
    return this.analystAgentConfigSnapshots.at(-1)!;
  }

  analystAgentConfigsForVersion(version?: number): AnalystAgentConfigSnapshot {
    return this.analystAgentConfigSnapshots.find((s) => s.version === version) ?? this.activeAnalystAgentConfigs();
  }

  get analystAgentConfigs(): AnalystAgentConfig[] {
    return this.activeAnalystAgentConfigs().configs;
  }

  // Rules & Routing (§10.6) - versioned the same way as every other Config section. Band
  // boundaries stay on the scoring model (they recompute scores, so they share that resource's
  // impact preview) - this covers thresholds, windows, triggers and the auto-triage rule.
  routingRulesSnapshots: RoutingRulesSnapshot[] = deepClone(ROUTING_RULES_VERSION_META);

  activeRoutingRules(): RoutingRulesSnapshot {
    return this.routingRulesSnapshots.at(-1)!;
  }

  routingRulesForVersion(version?: number): RoutingRulesSnapshot {
    return this.routingRulesSnapshots.find((s) => s.version === version) ?? this.activeRoutingRules();
  }

  /** Convenience read of the active version's rules - every existing call site that reads
   * `store.routingRules.<field>` keeps working unchanged against whatever is current. */
  get routingRules(): RoutingRules {
    return this.activeRoutingRules().rules;
  }

  // Classification Rules (§10.2) - versioned the same way as the field set (§3.6): a case
  // pins the version in force when it was classified, editing here never moves it mid-flight.
  classificationRulesSnapshots: ClassificationRulesSnapshot[] = CLASSIFICATION_RULES_VERSION_META.map((v) => ({
    ...v,
    signals: deepClone(CLASSIFICATION_SIGNALS),
    autoClearRules: deepClone(SCREENING_AUTO_CLEAR_RULES),
    triggerThresholds: deepClone(TRIGGER_PROPOSAL_THRESHOLDS),
  }));

  activeClassificationRules(): ClassificationRulesSnapshot {
    return this.classificationRulesSnapshots.at(-1)!;
  }

  classificationRulesForVersion(version?: number): ClassificationRulesSnapshot {
    return this.classificationRulesSnapshots.find((s) => s.version === version) ?? this.activeClassificationRules();
  }

  // Categories & Fields (§3, §10.1) - FCRM/analyst-owned, versioned. Every case pins the
  // version active when it started (§3.6); editing never moves an in-flight case.
  fieldSetSnapshots: FieldSetSnapshot[] = (() => {
    // The library, packs, core set and trigger catalog are identical across the seeded history -
    // only the threshold and version metadata actually differ. There's no real prior content to
    // reconstruct; this is enough for the history to read as a history (§9), not a single row.
    const triggerCategories: TriggerCategoryDef[] = ALL_CATEGORIES.map((cat) => ({
      key: cat,
      description: CATEGORY_DESCRIPTIONS[cat],
      amlCategory: TRIGGER_TO_AML[cat],
      classificationSignals: CLASSIFICATION_SIGNALS.filter((s) => s.triggerCategory === cat && s.kind === 'signal').map((s) => s.text),
      status: 'active' as const,
    }));
    return FIELD_SET_VERSIONS.map((v, i) => ({
      ...v,
      fieldLibrary: deepClone(FIELD_LIBRARY),
      coreSetKeys: deepClone(CORE_SET_KEYS),
      categoryPackKeys: deepClone(CATEGORY_PACK_KEYS),
      completionThreshold: FIELD_SET_VERSION_THRESHOLDS[i] ?? GLOBAL_COMPLETION_THRESHOLD,
      triggerToAmlCategory: deepClone(TRIGGER_TO_AML) as Record<TriggerCategory, string>,
      triggerCategories: deepClone(triggerCategories),
    }));
  })();

  activeFieldSet(): FieldSetSnapshot {
    return this.fieldSetSnapshots.at(-1)!;
  }

  fieldSetForVersion(version?: number): FieldSetSnapshot {
    return this.fieldSetSnapshots.find((s) => s.version === version) ?? this.activeFieldSet();
  }

  /** Core set plus every confirmed category's pack, deduplicated by field key (§3.4),
   * resolved against the field-set version the case is pinned to. */
  mergedRequiredFields(categories: TriggerCategory[], version?: number): RequiredFieldDef[] {
    const snap = this.fieldSetForVersion(version);
    const lib = new Map(snap.fieldLibrary.map((f) => [f.id, f]));
    const ids = new Map<string, RequiredFieldDef>();
    for (const key of snap.coreSetKeys) {
      const f = lib.get(key);
      if (f) ids.set(key, f);
    }
    for (const cat of categories) {
      for (const key of snap.categoryPackKeys[cat] ?? []) {
        const f = lib.get(key);
        if (f) ids.set(key, f);
      }
    }
    return Array.from(ids.values());
  }

  neverBlankFields(categories: TriggerCategory[], version?: number): RequiredFieldDef[] {
    return this.mergedRequiredFields(categories, version).filter((f) => f.neverBlank);
  }

  activeScoringModel(): ScoringModelVersion {
    return this.scoringModelVersions.find((m) => m.status === 'active') ?? this.scoringModelVersions[0];
  }

  getAnalystCase(id: string): AnalystCaseData {
    if (!this.analystCases[id]) {
      this.analystCases[id] = {
        caseId: id,
        analystAddedCategories: [],
        scoring: null,
        scoringHistory: [],
        controls: [],
        plannedControls: [],
        policyMappings: [],
        analysisRuns: [],
        recommendation: { narratives: [], decision: null, materialClarifications: [] },
        handoff: { kind: null, editsLog: [] },
        confirmedComponentIds: [],
        activityLog: [],
        flaggedValues: [],
        committeeVotes: [],
        committeeQueries: [],
      };
    }
    return this.analystCases[id];
  }

  private nextCaseNumber = 147;

  mintCaseId(): string {
    const id = `FCRM-2026-0${this.nextCaseNumber}`;
    this.nextCaseNumber += 1;
    return id;
  }

  getCase(id: string): CaseDetail | undefined {
    return this.cases.find((c) => c.id === id);
  }

  upsertCase(c: CaseDetail) {
    const idx = this.cases.findIndex((x) => x.id === c.id);
    if (idx >= 0) this.cases[idx] = c;
    else this.cases.unshift(c);
  }

  createDraft(): CaseDetail {
    const id = this.mintCaseId();
    const now = new Date().toISOString();
    const draft: CaseDetail = {
      id,
      title: `Draft - ${new Date().toLocaleDateString('en-US', { day: '2-digit', month: 'short', year: 'numeric' })}`,
      categories: [],
      status: 'Draft',
      analyst: null,
      submittedAt: null,
      closedAt: null,
      cycleDays: null,
      clarificationRounds: 0,
      completionPct: 0,
      slaTargetDate: null,
      createdAt: now,
      fields: [],
      optionalFields: [],
      documents: [],
      clarifications: [],
      activityLog: [
        { id: `${id}-a1`, timestamp: now, type: 'case_created', summary: 'Case created', actor: 'po' },
      ],
      chatTranscript: [],
      confidenceIndicator: 0,
      modelVersion: 'sonnet-5 · v2026.06',
      promptVersion: 'intake-prompt v14',
      threshold: this.activeFieldSet().completionThreshold,
      requiredFieldCount: 0,
      requiredFieldsFilled: 0,
      fieldSetVersion: this.activeFieldSet().version,
      classificationRulesVersion: this.activeClassificationRules().version,
      controlInventoryVersion: this.activeControlInventory().version,
      policyCorpusVersion: this.activePolicyCorpus().version,
      routingRulesVersion: this.activeRoutingRules().version,
      jurisdictionListVersion: this.activeJurisdictionRiskList().version,
    };
    this.cases.unshift(draft);
    return draft;
  }

  recalcRequiredCount(c: CaseDetail) {
    const total = this.mergedRequiredFields(c.categories, c.fieldSetVersion).length;
    c.requiredFieldCount = total;
    c.requiredFieldsFilled = c.fields.filter((f) => f.value).length;
  }

  constructor() {
    // Every case that has fields starts with a v1 submission snapshot, so the
    // Submission tab's version selector always has something to show.
    for (const c of this.cases) {
      if (c.fields.length > 0 && !c.submissionVersions) {
        c.submissionVersions = [
          {
            version: 1,
            capturedAt: c.submittedAt ?? c.createdAt,
            fieldValues: Object.fromEntries(c.fields.map((f) => [f.fieldId, f.value])),
            changedFieldIds: [],
          },
        ];
      }
    }
  }
}

export const store = new Store();
