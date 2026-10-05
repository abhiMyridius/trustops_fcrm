import { useEffect, useState } from 'react';
import type { CaseDetail } from '@/types';
import { getCase } from '@/services/api';
import { Modal } from '@/components/ui/Modal';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';

export function CaseViewModal({ caseId, onClose }: { caseId: string | null; onClose: () => void }) {
  const [c, setC] = useState<CaseDetail | null>(null);

  useEffect(() => {
    if (!caseId) {
      setC(null);
      return;
    }
    getCase(caseId).then(setC);
  }, [caseId]);

  return (
    <Modal open={!!caseId} onClose={onClose} title={caseId ?? undefined} width={560}>
      {!c ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-24" />
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div>
            <h3 className="text-[16px] font-bold text-text-primary">{c.title}</h3>
            <div className="flex items-center gap-2.5 mt-2">
              <StatusDot status={c.status} />
              {c.outcome && <Badge tone={c.outcome === 'Approved' ? 'success' : 'danger'}>{c.outcome}</Badge>}
              <span className="text-[12px] text-text-muted">{c.categories.join(', ')}</span>
            </div>
          </div>

          {c.decisionSummary && (
            <div className="bg-surface-tertiary rounded-md p-3.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">Decision summary</span>
              <p className="text-[13px] text-text-primary mt-1.5 leading-relaxed">{c.decisionSummary}</p>
            </div>
          )}

          <div className="flex flex-col gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">Key field values</span>
            {c.fields
              .filter((f) => f.value)
              .slice(0, 8)
              .map((f) => (
                <div key={f.fieldId} className="text-[12.5px]">
                  <span className="font-semibold text-text-secondary">{f.label}: </span>
                  <span className="text-text-primary">{f.value}</span>
                </div>
              ))}
          </div>
        </div>
      )}
    </Modal>
  );
}
