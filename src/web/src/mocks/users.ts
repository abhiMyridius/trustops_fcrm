/**
 * The identity directory the mock X1 serves: the 11 seeded users plus one synthetic observer
 * (ADR-19 names a "seeded fcrm_observer"; the seed has none yet, so the mock adds it).
 * Usernames are derived from the seed ids (u-pnair -> pnair). Demo passwords are synthetic and
 * only matter when VITE_DEMO_USERS=false; the picker never asks for one.
 */
import type { DemoUser, DemoUserGroup, Principal, Role } from '@/api/types/common';
import { seed } from './seed';

export interface DirectoryUser extends Principal {
  username: string;
  demo_password: string;
}

function fromSeed(u: { id: string; name: string; role: string; unit?: string }, role: Role): DirectoryUser {
  const username = u.id.replace(/^u-/, '');
  return { id: u.id, username, name: u.name, roles: [role], title: u.role, unit: u.unit, demo_password: `demo-${username}` };
}

export const DIRECTORY: DirectoryUser[] = [
  ...seed.users.po.map((u) => fromSeed(u, 'fcrm_po')),
  ...seed.users.analyst.map((u) => fromSeed(u, 'fcrm_analyst')),
  ...seed.users.committee.map((u) => fromSeed(u, 'fcrm_committee')),
  {
    id: 'u-observer',
    username: 'observer',
    name: 'Panel Observer',
    roles: ['fcrm_observer'],
    title: 'Observer (harness monitor only)',
    demo_password: 'demo-observer',
  },
];

export function userById(id: string): DirectoryUser | undefined {
  return DIRECTORY.find((u) => u.id === id);
}

export function nameOf(id: string): string {
  return userById(id)?.name ?? id;
}

export function principalOf(u: DirectoryUser): Principal {
  return { id: u.id, name: u.name, roles: u.roles, title: u.title, unit: u.unit };
}

const GROUP_LABEL: Record<Role, string> = {
  fcrm_po: 'Product owners',
  fcrm_analyst: 'FCRM analysts',
  fcrm_committee: 'Risk Committee',
  fcrm_observer: 'Observer',
};

export function demoUserGroups(): DemoUserGroup[] {
  const order: Role[] = ['fcrm_po', 'fcrm_analyst', 'fcrm_committee', 'fcrm_observer'];
  return order.map((role) => ({
    role,
    label: GROUP_LABEL[role],
    users: DIRECTORY.filter((u) => u.roles[0] === role).map(
      (u): DemoUser => ({ username: u.username, name: u.name, role, title: u.title, unit: u.unit }),
    ),
  }));
}

export const COMMITTEE_NAMES = seed.users.committee.map((u) => u.name);
