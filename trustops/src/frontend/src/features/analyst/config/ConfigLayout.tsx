/** 2.24 Config frame: eight sections. */
import { NavLink, Outlet } from 'react-router-dom';
import { PageHeader } from '@/components/layout/PageHeader';
import { cn } from '@/lib/cn';
import { useDocumentTitle } from '@/lib/a11y';

export const SECTIONS: { key: string; label: string }[] = [
  { key: 'field-sets', label: 'Categories & Fields' },
  { key: 'categories', label: 'Classification Rules' },
  { key: 'scoring-model', label: 'Scoring Model' },
  { key: 'control-inventory', label: 'Control Inventory' },
  { key: 'policy-corpus', label: 'Policy Corpus' },
  { key: 'routing-rules', label: 'Rules & Routing' },
  { key: 'jurisdiction-risk', label: 'Jurisdiction Risk List' },
  { key: 'ai-agents', label: 'AI Agents' },
];

export function ConfigLayout() {
  useDocumentTitle('Config');
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Configuration" />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[220px_1fr]">
        <nav aria-label="Configuration sections" className="flex flex-row gap-1 overflow-x-auto lg:flex-col">
          {SECTIONS.map((s) => (
            <NavLink
              key={s.key}
              to={`/analyst/config/${s.key}`}
              className={({ isActive }) =>
                cn(
                  'whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium hover:no-underline',
                  isActive ? 'bg-virtual-tide-tint font-semibold text-quantum-leaf' : 'text-text-secondary hover:bg-surface',
                )
              }
            >
              {s.label}
            </NavLink>
          ))}
        </nav>
        <div className="min-w-0">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
