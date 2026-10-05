import type { Template, TriggerCategory } from '@/types';
import { CATEGORY_SPECIFIC_FIELDS, COMMON_FIELDS, DEFAULT_COMPLETION_THRESHOLD } from './requiredFields';

function fieldsFor(category: TriggerCategory) {
  return [...COMMON_FIELDS, ...CATEGORY_SPECIFIC_FIELDS[category]];
}

export const CATEGORY_DESCRIPTIONS: Record<TriggerCategory, string> = {
  'New Product': 'A product or service being offered to customers for the first time, including pilots and limited launches.',
  'New Feature': 'A material change or addition to an existing product, where the product itself is already assessed.',
  'Process Change': 'A change to how an existing product or service is operated, delivered or serviced.',
  'Vendor / Third-Party Onboarding': 'Engaging an external party that will handle customer data, funds, or a regulated step in a process.',
  'New Geography': 'Offering an existing or new product into a market, corridor or jurisdiction not previously served.',
  'New Customer Segment': 'Serving a customer type the bank does not currently serve, or serving an existing type through a new channel.',
};

export const TEMPLATES: Record<TriggerCategory, Template> = {
  'Vendor / Third-Party Onboarding': {
    category: 'Vendor / Third-Party Onboarding',
    version: 2,
    requiredFields: fieldsFor('Vendor / Third-Party Onboarding'),
    optionalFields: [
      { id: 'cost_centre_tag', label: 'Cost-centre tag', order: 1, type: 'text' },
      { id: 'vendor_contract_ref', label: 'Vendor contract reference #', order: 2, type: 'text' },
    ],
    completionThreshold: DEFAULT_COMPLETION_THRESHOLD,
    aiInsight:
      'Vendor data-retention scope needed a clarification round on all three of your vendor-onboarding cases, each time because the answer did not state the retention period and its regulatory basis. Covering both during intake would likely save a round.',
    // Pack history only - optional fields are PO-owned and never appear here, and the core set
    // (data-retention scope lives in this pack, not there) keeps its own separate history.
    history: [
      { version: 1, publishedAt: '2025-11-03', note: 'Initial FCRM-authored vendor pack.' },
      { version: 2, publishedAt: '2026-02-14', note: 'Added vendor data-retention scope as required.' },
    ],
  },
  'New Product': {
    category: 'New Product',
    version: 2,
    requiredFields: fieldsFor('New Product'),
    optionalFields: [{ id: 'project_code', label: 'Project code', order: 1, type: 'text' }],
    completionThreshold: DEFAULT_COMPLETION_THRESHOLD,
    aiInsight:
      'No recurring clarification pattern detected yet across New Product submissions - sample size (2 closed cases) is too small for a reliable suggestion.',
    history: [
      { version: 1, publishedAt: '2025-12-01', note: 'Initial FCRM-authored template.' },
      { version: 2, publishedAt: '2026-04-10', note: 'FCRM approved PO-added project code optional field.' },
    ],
  },
  'New Geography': {
    category: 'New Geography',
    version: 1,
    requiredFields: fieldsFor('New Geography'),
    optionalFields: [],
    completionThreshold: DEFAULT_COMPLETION_THRESHOLD,
    aiInsight:
      'No recurring clarification pattern detected yet across New Geography submissions - sample size (2 closed cases) is too small for a reliable suggestion.',
    history: [{ version: 1, publishedAt: '2025-10-20', note: 'Initial FCRM-authored template.' }],
  },
  'New Feature': {
    category: 'New Feature',
    version: 1,
    requiredFields: fieldsFor('New Feature'),
    optionalFields: [],
    completionThreshold: DEFAULT_COMPLETION_THRESHOLD,
    aiInsight: 'Not enough submissions in this category yet to suggest changes.',
    history: [{ version: 1, publishedAt: '2025-09-15', note: 'Initial FCRM-authored template.' }],
  },
  'Process Change': {
    category: 'Process Change',
    version: 1,
    requiredFields: fieldsFor('Process Change'),
    optionalFields: [],
    completionThreshold: DEFAULT_COMPLETION_THRESHOLD,
    aiInsight: 'Not enough submissions in this category yet to suggest changes.',
    history: [{ version: 1, publishedAt: '2025-09-15', note: 'Initial FCRM-authored template.' }],
  },
  'New Customer Segment': {
    category: 'New Customer Segment',
    version: 1,
    requiredFields: fieldsFor('New Customer Segment'),
    optionalFields: [],
    completionThreshold: DEFAULT_COMPLETION_THRESHOLD,
    aiInsight: 'Not enough submissions in this category yet to suggest changes.',
    history: [{ version: 1, publishedAt: '2025-09-15', note: 'Initial FCRM-authored template.' }],
  },
};
