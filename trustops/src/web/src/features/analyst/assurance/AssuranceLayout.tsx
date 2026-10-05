import { Outlet } from 'react-router-dom';
import { PageHeader } from '@/components/layout/PageHeader';
import { RouteTabs } from '@/components/ui/Tabs';
import { useDocumentTitle } from '@/lib/a11y';

/** Assurance: examiner Q&A, overrides, watchlist re-flags and agent quality (design C §2.22–2.23). */
export function AssuranceLayout() {
  useDocumentTitle('Assurance');
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Assurance" subtitle="Questions answered only from the audit chain, the override log, watchlist re-flags and how each agent is doing." />
      <RouteTabs
        base="/analyst/assurance"
        label="Assurance sections"
        tabs={[
          { key: 'ask', label: 'Ask' },
          { key: 'overrides', label: 'Overrides' },
          { key: 'watchlist', label: 'Watchlist' },
          { key: 'agent-quality', label: 'Agent quality' },
        ]}
      >
        <Outlet />
      </RouteTabs>
    </div>
  );
}
