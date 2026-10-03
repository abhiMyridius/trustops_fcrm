import type { ReactNode } from 'react';
import { Inbox, RotateCcw, TriangleAlert } from 'lucide-react';
import { ApiError, userMessage } from '@/api/errors';
import { getTraceId } from '@/api/client';
import { Button } from './Button';

export function EmptyState({ title, body, action, icon }: { title: string; body?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border-strong bg-surface px-6 py-10 text-center">
      <span aria-hidden className="text-text-faint">
        {icon ?? <Inbox size={28} />}
      </span>
      <p className="text-sm font-semibold text-text-primary">{title}</p>
      {body && <p className="max-w-md text-[13px] text-text-muted">{body}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Inline query error with the server message, the trace id and Retry; the rest of the page stays usable. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const trace = error instanceof ApiError ? error.traceId : getTraceId();
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-lg border border-danger bg-danger-bg px-5 py-4">
      <p className="flex items-center gap-2 text-sm font-semibold text-danger">
        <TriangleAlert aria-hidden size={16} />
        {userMessage(error)}
      </p>
      <p className="text-xs text-text-secondary">Trace id {trace}</p>
      {onRetry && (
        <div>
          <Button size="sm" variant="secondary" onClick={onRetry} icon={<RotateCcw aria-hidden size={14} />}>
            Retry
          </Button>
        </div>
      )}
    </div>
  );
}
