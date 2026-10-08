/**
 * Roles come from the access token's `roles` claim (ADR-10). The UI uses them for navigation and
 * layout only; field-level visibility is decided by the server (a PO payload has no score to hide).
 */
import type { Role } from '@/api/types/common';

export type { Role };

export const ROLE_LABEL: Record<Role, string> = {
  fcrm_po: 'Product owner',
  fcrm_analyst: 'FCRM analyst',
  fcrm_committee: 'Risk Committee',
  fcrm_observer: 'Observer (monitor only)',
};

/** The demo case every observer lands on (ADR-19). */
export const DEMO_CASE_ID = 'FCRM-2026-0151';

export function homeFor(role: Role): string {
  switch (role) {
    case 'fcrm_po':
      return '/po';
    case 'fcrm_analyst':
      return '/analyst';
    case 'fcrm_committee':
      return '/committee';
    case 'fcrm_observer':
      return `/monitor/${DEMO_CASE_ID}`;
  }
}

/** The person's primary role: the first FCRM role in the claim, in a fixed precedence. */
export function primaryRole(roles: readonly Role[]): Role | null {
  for (const r of ['fcrm_analyst', 'fcrm_committee', 'fcrm_po', 'fcrm_observer'] as const) {
    if (roles.includes(r)) return r;
  }
  return null;
}
