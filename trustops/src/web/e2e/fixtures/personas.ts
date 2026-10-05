/**
 * The demo cast's real names and roles (src/web/src/mocks/users.ts `DIRECTORY`, itself derived
 * from the P7 seed's `users` section), used to drive the seeded-user picker on the login page
 * (`VITE_DEMO_USERS=true`, the default): click "Sign in with corporate SSO", then click the
 * button containing the person's name.
 */
export interface Persona {
  name: string;
  role: 'fcrm_po' | 'fcrm_analyst' | 'fcrm_committee' | 'fcrm_observer';
  /** Where `homeFor(role)` (src/web/src/auth/roles.ts) sends them after login. */
  home: string;
}

export const PRIYA: Persona = { name: 'Priya Nair', role: 'fcrm_po', home: '/' };
export const RAHUL: Persona = { name: 'Rahul Iyer', role: 'fcrm_analyst', home: '/analyst' };
export const ANA: Persona = { name: 'Ana Mendes', role: 'fcrm_committee', home: '/committee' };
