import type { ReactNode } from 'react';
import { Breadcrumb, type Crumb } from '@/components/ui/Breadcrumb';

/** Page title block: breadcrumb, the single h1 of the page, a subtitle and actions. */
export function PageHeader({ title, subtitle, crumbs, actions, meta }: { title: ReactNode; subtitle?: ReactNode; crumbs?: Crumb[]; actions?: ReactNode; meta?: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      {crumbs && <Breadcrumb items={crumbs} />}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[22px] font-extrabold leading-tight tracking-tight text-text-primary">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-text-secondary">{subtitle}</p>}
          {meta && <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div>}
        </div>
        {actions && <div className="no-print flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
