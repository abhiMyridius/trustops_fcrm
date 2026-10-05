import type { ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

export function EmptyState({
  icon = 'folder',
  title,
  description,
  action,
}: {
  icon?: IconName;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center text-center gap-3 py-14 px-6">
      <div className="w-12 h-12 rounded-full bg-surface-tertiary flex items-center justify-center text-text-muted">
        <Icon name={icon} size={22} />
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-sm font-semibold text-text-primary">{title}</span>
        {description && <span className="text-[13px] text-text-muted max-w-sm">{description}</span>}
      </div>
      {action}
    </div>
  );
}
