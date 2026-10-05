import type { Notification, NotificationEventPref } from '@/types';

// Three events only - Case auto-cleared and Submission validation failed were dropped because
// the PO is present, in the app, watching both happen synchronously; a notification about
// either is noise (§1 of the settings v2 mod prompt).
export const DEFAULT_NOTIFICATION_EVENTS: NotificationEventPref[] = [
  { event: 'clarification_requested', label: 'Clarification requested by analyst', enabled: true, frequency: 'Immediate' },
  { event: 'decision_reached', label: 'Decision reached', enabled: true, frequency: 'Immediate' },
  { event: 'sla_approaching', label: 'SLA approaching breach', enabled: false, frequency: 'Daily digest' },
];

export const SEED_NOTIFICATIONS: Notification[] = [
  {
    id: 'n1',
    caseId: 'FCRM-2026-0139',
    title: 'Clarification requested',
    body: 'Priya Raman asked about vendor data-retention scope on FCRM-2026-0139.',
    timestamp: '2026-08-25T09:00:00Z',
    read: true,
    type: 'clarification_requested',
  },
  {
    id: 'n2',
    caseId: 'FCRM-2026-0139',
    title: 'Case moved to Committee Review',
    body: 'FCRM-2026-0139 passed analyst assessment and moved to Committee Review.',
    timestamp: '2026-08-29T10:00:00Z',
    read: true,
    type: 'decision_reached',
  },
  {
    id: 'n3',
    caseId: 'FCRM-2026-0133',
    title: 'Case assigned',
    body: 'FCRM-2026-0133 was assigned to Daniel Okafor and moved to Under Assessment.',
    timestamp: '2026-08-03T09:35:00Z',
    read: true,
    type: 'decision_reached',
  },
  {
    id: 'n4',
    caseId: null,
    title: 'SLA approaching breach',
    body: 'FCRM-2026-0139 is 6 business days from its SLA target.',
    timestamp: '2026-09-01T08:00:00Z',
    read: false,
    type: 'sla_approaching',
  },
  {
    id: 'n5',
    caseId: 'FCRM-2026-0126',
    title: 'FCRM-2026-0126 closed - Rejected',
    body: "Approval was declined because the vendor could not evidence a recent independent AML audit or commit to a fixed data-retention period. See the case Overview for what a resubmission would need.",
    timestamp: '2026-05-27T15:01:00Z',
    read: true,
    type: 'decision_reached',
  },
  {
    id: 'n6',
    caseId: 'FCRM-2026-0136',
    title: 'FCRM-2026-0136 closed - Approved',
    body: "The referral partner's independent license and in-house KYC supported the submission as-is. Ready to proceed.",
    timestamp: '2026-06-25T13:01:00Z',
    read: true,
    type: 'decision_reached',
  },
];
