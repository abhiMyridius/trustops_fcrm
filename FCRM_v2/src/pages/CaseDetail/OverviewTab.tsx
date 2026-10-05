import { useState } from 'react';
import type { CaseDetail, Document } from '@/types';
import { Card } from '@/components/ui/Card';
import { Icon } from '@/components/ui/Icon';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { formatDate } from '@/utils/format';
import { DocumentViewModal } from './DocumentViewModal';
import { SubmissionVersionView } from '@/components/shared/SubmissionVersionView';

export function OverviewTab({ caseData, onReload }: { caseData: CaseDetail; onReload: () => void }) {
  const decided = caseData.status === 'Closed' && caseData.outcome;
  const [viewingDoc, setViewingDoc] = useState<Document | null>(null);

  return (
    <div className="flex flex-col gap-6">
      <Card material="flat" className="p-5">
        <span className="text-sm font-bold text-text-primary mb-4 block">Submission</span>
        <SubmissionVersionView caseData={caseData} editable={caseData.status !== 'Closed'} onFieldEdited={onReload} onViewDocument={setViewingDoc} />
      </Card>

      {caseData.optionalFields.length > 0 && (
        <Card material="flat" className="p-5">
          <span className="text-sm font-bold text-text-primary mb-4 block">Additional fields</span>
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4">
            {caseData.optionalFields.map((f) => (
              <div key={f.fieldId} className="flex flex-col gap-0.5">
                <span className="text-[11.5px] font-semibold text-text-muted">{f.label}</span>
                <span className="text-[13.5px] text-text-primary">{f.value}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card material="flat" className="p-5">
        <span className="text-sm font-bold text-text-primary mb-4 block">Documents</span>
        {caseData.documents.length === 0 ? (
          <p className="text-[13px] text-text-faint">No documents yet.</p>
        ) : (
          <div className="flex flex-col divide-y divide-border-hairline">
            {caseData.documents.map((d) => (
              <div key={d.id} className="flex items-center gap-3 py-2.5">
                <Icon name="document" size={16} className="text-text-muted shrink-0" />
                <span className="flex-1 text-[13px] text-text-primary truncate">{d.name}</span>
                <span className="text-[11px] text-text-faint">v{d.version}</span>
                <Badge tone="default">{d.source === 'uploaded' ? 'Uploaded' : d.integrationName ?? 'Integration'}</Badge>
                <span className="text-[11.5px] text-text-faint w-24 text-right">{formatDate(d.uploadedAt)}</span>
                <Button size="sm" variant="ghost" icon={<Icon name="external-link" size={13} />} onClick={() => setViewingDoc(d)}>
                  View
                </Button>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card material="flat" className="p-5">
        <span className="text-sm font-bold text-text-primary mb-3 block">Decision</span>
        {!decided ? (
          <Badge tone="default">Pending</Badge>
        ) : (
          <div className="flex flex-col gap-2">
            <Badge tone={caseData.outcome === 'Approved' ? 'success' : 'danger'}>{caseData.outcome}</Badge>
            {caseData.decisionSummary && <p className="text-[13.5px] text-text-primary leading-relaxed mt-1">{caseData.decisionSummary}</p>}
          </div>
        )}
      </Card>

      <DocumentViewModal doc={viewingDoc} onClose={() => setViewingDoc(null)} />
    </div>
  );
}
