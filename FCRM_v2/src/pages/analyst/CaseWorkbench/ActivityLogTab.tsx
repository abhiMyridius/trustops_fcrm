import { useEffect, useState } from 'react';
import type { CaseDetail } from '@/types';
import { getAnalystCase } from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Skeleton } from '@/components/ui/Skeleton';
import { formatDateTime } from '@/utils/format';
import { useToast } from '@/components/ui/Toast';

interface MergedEvent {
  id: string;
  timestamp: string;
  summary: string;
  detail?: string;
  actor: string;
  modelVersion?: string;
}

export function ActivityLogTab({ caseData }: { caseData: CaseDetail }) {
  const [events, setEvents] = useState<MergedEvent[] | null>(null);
  const toast = useToast();

  useEffect(() => {
    getAnalystCase(caseData.id).then((a) => {
      const merged: MergedEvent[] = [
        ...caseData.activityLog.map((e) => ({ id: e.id, timestamp: e.timestamp, summary: e.summary, detail: e.detail, actor: e.actor, modelVersion: e.modelVersion })),
        ...a.activityLog.map((e) => ({ id: e.id, timestamp: e.timestamp, summary: e.summary, detail: e.detail, actor: e.actor, modelVersion: e.modelVersion })),
      ].sort((a, b) => (a.timestamp > b.timestamp ? 1 : -1));
      setEvents(merged);
    });
  }, [caseData]);

  if (!events) return <Skeleton className="h-96 rounded-md" />;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <Button size="sm" variant="secondary" icon={<Icon name="download" size={14} />} onClick={() => toast(`Exported ${caseData.id} case record.`, 'success')}>
          Export case record
        </Button>
      </div>
      <Card material="flat" className="p-5">
        <div className="flex flex-col">
          {events.map((e, i) => (
            <div key={e.id} className="flex gap-3 pb-4 relative">
              {i < events.length - 1 && <div className="absolute left-[7px] top-4 bottom-0 w-px bg-border" />}
              <div className="w-3.5 h-3.5 rounded-full bg-carbon-moss shrink-0 mt-1" />
              <div className="flex-1 min-w-0 pb-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[13px] font-semibold text-text-primary">{e.summary}</span>
                  <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint bg-surface-tertiary px-1.5 py-0.5 rounded">{e.actor}</span>
                </div>
                {e.detail && <p className="text-[12.5px] text-text-secondary mt-1 leading-relaxed">{e.detail}</p>}
                <div className="text-[11px] text-text-faint mt-1">
                  {formatDateTime(e.timestamp)}
                  {e.modelVersion && ` · ${e.modelVersion}`}
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
