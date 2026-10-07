/** 2.9 PO Activity: the server's allow-listed projection (no agent names, models, scores, overrides or votes). */
import { useParams } from 'react-router-dom';
import { useActivityPO } from '@/api/queries/caseQueries';
import type { ActivityEventPO } from '@/api/types/po';
import { AiMark } from '@/components/ui/AiMark';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { cn } from '@/lib/cn';
import { useDocumentTitle } from '@/lib/a11y';
import { formatDateTime } from '@/lib/format';

const ACTOR_TONE: Record<ActivityEventPO['actor'], string> = {
  You: 'bg-pulse-green-tint-3 text-quantum-leaf',
  FCRM: 'bg-info-bg text-chart-deep-1',
  System: 'bg-surface-secondary text-text-secondary',
  AI: 'bg-warning-bg text-warning-text',
};

export function POActivityTab() {
  const { id = '' } = useParams();
  useDocumentTitle('Activity', id);
  const q = useActivityPO(id);
  return (
    <QueryState query={q} label="Loading activity">
      {(events) =>
        events.length === 0 ? (
          <EmptyState title="No activity yet" />
        ) : (
          <ol className="flex max-w-3xl flex-col gap-0 border-l-2 border-border pl-5" aria-label="Case activity, newest first">
            {events.map((e) => (
              <li key={e.id} className="relative pb-4">
                <span aria-hidden className="absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 border-surface bg-ink-7" />
                <p className="flex flex-wrap items-center gap-2 text-sm">
                  <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold', ACTOR_TONE[e.actor])}>
                    {e.actor === 'AI' && <AiMark size={11} />}
                    {e.actor}
                  </span>
                  {e.text}
                </p>
                <p className="mt-0.5 text-[11px] text-text-muted">{formatDateTime(e.at)}</p>
              </li>
            ))}
          </ol>
        )
      }
    </QueryState>
  );
}
