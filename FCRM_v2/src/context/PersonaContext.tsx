import { createContext, useContext, useState, type ReactNode } from 'react';

export type Persona = 'po' | 'analyst' | 'committee';

export const PO_USER = { name: 'Arun Mehta', role: 'Product Owner', team: 'Payments & Cross-Border', initials: 'AM' };
export const ANALYST_USER = { name: 'Priya Raman', role: 'FCRM Analyst', team: 'Financial Crimes Risk Management', initials: 'PR' };
export const COMMITTEE_USER = { name: 'Ravi Menon', role: 'Risk Committee', team: 'Risk Committee', initials: 'RM' };

interface PersonaContextValue {
  persona: Persona;
  setPersona: (p: Persona) => void;
  user: typeof PO_USER | typeof ANALYST_USER | typeof COMMITTEE_USER;
}

const PersonaContext = createContext<PersonaContextValue | null>(null);

export function personaFromPath(pathname: string): Persona {
  if (pathname.startsWith('/committee')) return 'committee';
  if (pathname.startsWith('/analyst')) return 'analyst';
  return 'po';
}

function userForPersona(persona: Persona) {
  if (persona === 'analyst') return ANALYST_USER;
  if (persona === 'committee') return COMMITTEE_USER;
  return PO_USER;
}

export function PersonaProvider({ children }: { children: ReactNode }) {
  const [persona, setPersona] = useState<Persona>(() => personaFromPath(window.location.pathname));
  const user = userForPersona(persona);
  return <PersonaContext.Provider value={{ persona, setPersona, user }}>{children}</PersonaContext.Provider>;
}

export function usePersona() {
  const ctx = useContext(PersonaContext);
  if (!ctx) throw new Error('usePersona must be used within PersonaProvider');
  return ctx;
}
