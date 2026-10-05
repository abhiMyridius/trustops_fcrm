import type { CaseDetail } from '@/types';
import { Card } from '@/components/ui/Card';
import { Icon } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';
import { formatDateTime } from '@/utils/format';
import { useToast } from '@/components/ui/Toast';

const ACTOR_LABEL: Record<string, string> = { ai: 'AI', po: 'You', analyst: 'Analyst', system: 'System' };

export function ActivityLogTab({ caseData }: { caseData: CaseDetail }) {
  const toast = useToast();

  function handleExport() {
    toast(`Exported ${caseData.id} case record.`, 'success');
  }

  const events = caseData.activityLog.slice().sort((a, b) => (a.timestamp > b.timestamp ? 1 : -1));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <Button size="sm" variant="secondary" icon={<Icon name="download" size={14} />} onClick={handleExport}>
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
                  <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint bg-surface-tertiary px-1.5 py-0.5 rounded">{ACTOR_LABEL[e.actor]}</span>
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
