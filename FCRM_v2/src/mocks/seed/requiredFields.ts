import type { RequiredFieldDef, TriggerCategory } from '@/types';
import { ALL_CATEGORIES } from '@/types';

// Field Library (§3.1 of the analyst build prompt). Every field is defined
// exactly once here. The Core Set and Category Packs below only ever
// reference a field by key - nothing else defines a field. A key is
// immutable once created, because submitted case data references it.
export const FIELD_LIBRARY: RequiredFieldDef[] = [
  // Core set (9) - required on every submission regardless of category.
  {
    id: 'business_description',
    label: 'Business description & rationale',
    category: 'Common',
    neverBlank: true,
    type: 'textarea',
    status: 'active',
    helpText: 'What is changing and why - written so someone outside your team can follow it.',
  },
  {
    id: 'trigger_categories',
    label: 'Trigger categories',
    category: 'Common',
    neverBlank: true,
    type: 'multiselect',
    status: 'active',
    helpText: 'Which of the six change types this submission falls under - you can select more than one.',
  },
  {
    id: 'target_customer_segments',
    label: 'Target customer segment(s)',
    category: 'Common',
    type: 'textarea',
    status: 'active',
    helpText: 'Who this reaches - existing customers, a new segment, or a specific corridor or channel.',
  },
  {
    id: 'expected_annual_volume',
    label: 'Expected annual volume',
    category: 'Common',
    type: 'text',
    status: 'active',
    helpText: 'Rough transaction or customer count in year one - an estimate is fine.',
  },
  {
    id: 'expected_annual_revenue',
    label: 'Expected annual revenue',
    category: 'Common',
    type: 'text',
    status: 'active',
    helpText: "Rough revenue impact in year one - we won't estimate this one for you.",
  },
  {
    id: 'related_prior_assessments',
    label: 'Related prior products or assessments',
    category: 'Common',
    type: 'text',
    status: 'active',
    helpText: 'Any earlier FCRM case this builds on or resembles - reference the case ID if you know it.',
  },
  {
    id: 'supporting_documents',
    label: 'Supporting documents',
    category: 'Common',
    type: 'documents',
    status: 'active',
    helpText: 'A business case, contract, or diagram - whatever you already have. We pull what we can from it.',
  },
  {
    id: 'target_launch_date',
    label: 'Target launch date',
    category: 'Common',
    type: 'date',
    status: 'active',
    helpText: 'Your current target - it can move later without needing a new case.',
  },
  {
    id: 'business_sponsor',
    label: 'Business sponsor',
    category: 'Common',
    neverBlank: true,
    type: 'text',
    status: 'active',
    helpText: 'Who on the business side owns this change and can answer questions about it.',
  },

  // Vendor / Third-Party Onboarding pack (5).
  {
    id: 'vendor_legal_name_jurisdiction',
    label: 'Vendor legal name & jurisdiction',
    category: 'Vendor / Third-Party Onboarding',
    neverBlank: true,
    type: 'text',
    status: 'active',
    helpText: 'The vendor\'s full legal entity name and where it is incorporated or licensed.',
  },
  {
    id: 'vendor_role',
    label: 'Vendor role in the process',
    category: 'Vendor / Third-Party Onboarding',
    type: 'textarea',
    status: 'active',
    helpText: 'What the vendor actually does in the flow - the specific step or function they perform.',
  },
  {
    id: 'vendor_data_access_scope',
    label: 'Vendor data-access scope',
    category: 'Vendor / Third-Party Onboarding',
    type: 'textarea',
    status: 'active',
    helpText: 'Exactly what customer or transaction data the vendor can see - name fields, amounts, identifiers.',
  },
  {
    id: 'vendor_data_retention_scope',
    label: 'Vendor data-retention scope',
    category: 'Vendor / Third-Party Onboarding',
    neverBlank: true,
    type: 'textarea',
    status: 'active',
    helpText: 'How long the vendor keeps the data it accesses, and what happens to it after that.',
  },
  // Shared with the Process Change pack below - a field can belong to more than one pack.
  {
    id: 'process_data_flow',
    label: 'Process / data-flow description',
    category: 'Vendor / Third-Party Onboarding',
    type: 'textarea',
    status: 'active',
    helpText: 'How data moves through the process, step by step - a diagram works well here.',
  },

  // New Geography pack (1).
  {
    id: 'geographies_in_scope',
    label: 'Geographies in scope',
    category: 'New Geography',
    neverBlank: true,
    type: 'textarea',
    status: 'active',
    helpText: 'Every country or corridor this change touches, including origination and destination.',
  },

  // New Product pack (3).
  {
    id: 'product_structure_pricing',
    label: 'Product structure and pricing',
    category: 'New Product',
    type: 'textarea',
    status: 'active',
    helpText: 'How the product works and how it is priced - fees, rates, or revenue structure.',
  },
  {
    id: 'product_kyc_process',
    label: 'Customer onboarding and KYC process',
    category: 'New Product',
    type: 'textarea',
    status: 'active',
    helpText: 'How a customer is verified and onboarded for this product - new steps or the existing flow.',
  },
  {
    id: 'product_transaction_limits',
    label: 'Transaction limits and controls at launch',
    category: 'New Product',
    type: 'textarea',
    status: 'active',
    helpText: 'Any caps, limits, or monitoring rules in place when this launches.',
  },

  // New Feature pack (3).
  {
    id: 'feature_scope_change',
    label: 'Feature description & scope of change',
    category: 'New Feature',
    type: 'textarea',
    status: 'active',
    helpText: 'What is being added or changed on the existing product, and how big the change is.',
  },
  {
    id: 'impacted_existing_products',
    label: 'Impacted existing products/services',
    category: 'New Feature',
    type: 'text',
    status: 'active',
    helpText: 'Which already-assessed products or services this feature touches.',
  },
  {
    id: 'feature_control_changes',
    label: 'Control changes required',
    category: 'New Feature',
    type: 'textarea',
    status: 'active',
    helpText: 'Whether existing controls need to change to cover this feature, or none do.',
  },

  // Process Change pack (4) - process_data_flow above is shared into this pack too.
  {
    id: 'process_current_state',
    label: 'Current process',
    category: 'Process Change',
    type: 'textarea',
    status: 'active',
    helpText: 'How the process works today, before this change.',
  },
  {
    id: 'process_changed_state',
    label: 'Changed process',
    category: 'Process Change',
    type: 'textarea',
    status: 'active',
    helpText: 'How the process will work after this change - what specifically is different.',
  },
  {
    id: 'systems_controls_impacted',
    label: 'Systems or controls impacted',
    category: 'Process Change',
    type: 'text',
    status: 'active',
    helpText: 'Which systems or existing controls this change touches.',
  },

  // New Customer Segment pack (3).
  {
    id: 'segment_definition',
    label: 'Segment definition',
    category: 'New Customer Segment',
    type: 'textarea',
    status: 'active',
    helpText: 'Who is in scope for this segment and what makes it distinct from customers you already serve.',
  },
  {
    id: 'segment_onboarding_path',
    label: 'Onboarding path',
    category: 'New Customer Segment',
    type: 'textarea',
    status: 'active',
    helpText: 'How this segment is verified and onboarded - the same path as existing customers, or a new one.',
  },
  {
    id: 'segment_expected_risk_profile',
    label: 'Expected risk profile',
    category: 'New Customer Segment',
    type: 'textarea',
    status: 'active',
    helpText: 'What, if anything, is different about the risk profile of this segment versus customers you already serve.',
  },
];

const byKey = new Map(FIELD_LIBRARY.map((f) => [f.id, f]));

export function fieldByKey(key: string): RequiredFieldDef | undefined {
  return byKey.get(key);
}

// Core Set - field keys required on every submission, regardless of category.
export const CORE_SET_KEYS: string[] = [
  'business_description',
  'trigger_categories',
  'target_customer_segments',
  'expected_annual_volume',
  'expected_annual_revenue',
  'related_prior_assessments',
  'supporting_documents',
  'target_launch_date',
  'business_sponsor',
];

// Category Packs - field keys added per trigger category, beyond the core set.
// A key may appear in more than one pack (process_data_flow appears in two) -
// that is correct and handled by the dedup in mergedRequiredFields.
export const CATEGORY_PACK_KEYS: Record<TriggerCategory, string[]> = {
  'Vendor / Third-Party Onboarding': ['vendor_legal_name_jurisdiction', 'vendor_role', 'vendor_data_access_scope', 'vendor_data_retention_scope', 'process_data_flow'],
  'New Geography': ['geographies_in_scope'],
  'New Product': ['product_structure_pricing', 'product_kyc_process', 'product_transaction_limits'],
  'New Feature': ['feature_scope_change', 'impacted_existing_products', 'feature_control_changes'],
  'Process Change': ['process_current_state', 'process_changed_state', 'systems_controls_impacted', 'process_data_flow'],
  'New Customer Segment': ['segment_definition', 'segment_onboarding_path', 'segment_expected_risk_profile'],
};

export const COMMON_FIELDS: RequiredFieldDef[] = CORE_SET_KEYS.map((k) => byKey.get(k)!);

export const CATEGORY_SPECIFIC_FIELDS: Record<TriggerCategory, RequiredFieldDef[]> = Object.fromEntries(
  ALL_CATEGORIES.map((cat) => [cat, CATEGORY_PACK_KEYS[cat].map((k) => byKey.get(k)!)]),
) as Record<TriggerCategory, RequiredFieldDef[]>;

/** Union of required fields for a set of selected categories = core set plus every confirmed
 * category's pack, deduplicated by field key (§3.4). Unweighted. */
export function mergedRequiredFields(categories: TriggerCategory[]): RequiredFieldDef[] {
  const ids = new Map<string, RequiredFieldDef>();
  for (const key of CORE_SET_KEYS) ids.set(key, byKey.get(key)!);
  for (const cat of categories) {
    for (const key of CATEGORY_PACK_KEYS[cat] ?? []) ids.set(key, byKey.get(key)!);
  }
  return Array.from(ids.values());
}

/** Never-blank field defs in scope for a set of selected categories - the second, independent
 * submission-gate condition alongside the completion threshold (§3.5). */
export function neverBlankFields(categories: TriggerCategory[]): RequiredFieldDef[] {
  return mergedRequiredFields(categories).filter((f) => f.neverBlank);
}

// Core set and each category pack are separately versioned FCRM-owned artifacts - the core set
// applies identically across every category, so it carries its own version history rather than
// borrowing whichever pack's happens to be open.
export const CORE_SET_VERSION = 1;
export const CORE_SET_HISTORY: { version: number; publishedAt: string; note: string }[] = [
  { version: 1, publishedAt: '2025-09-01', note: 'Initial FCRM-authored core set.' },
];
export const CORE_SET_LAST_UPDATED = CORE_SET_HISTORY.at(-1)!.publishedAt;

/** One global completion threshold, set in one place, applies to every submission (§3.5). */
export const GLOBAL_COMPLETION_THRESHOLD = 80;
/** @deprecated use GLOBAL_COMPLETION_THRESHOLD - kept as an alias so existing call sites don't break. */
export const DEFAULT_COMPLETION_THRESHOLD = GLOBAL_COMPLETION_THRESHOLD;
