import type { Document } from '@/types';
import { Modal } from '@/components/ui/Modal';
import { Icon } from '@/components/ui/Icon';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { formatDateTime } from '@/utils/format';
import { useToast } from '@/components/ui/Toast';
import { DocumentPreview } from './DocumentPreview';

function extOf(name: string) {
  const parts = name.split('.');
  return parts.length > 1 ? parts[parts.length - 1].toUpperCase() : 'FILE';
}

export function DocumentViewModal({ doc, onClose }: { doc: Document | null; onClose: () => void }) {
  const toast = useToast();

  return (
    <Modal open={!!doc} onClose={onClose} title={doc?.name} width={640}>
      {doc && (
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-4">
            <div className="w-10 h-12 rounded-md bg-carbon-moss-deep flex items-center justify-center shrink-0">
              <span className="text-[9px] font-bold text-quantum-leaf">{extOf(doc.name)}</span>
            </div>
            <div className="flex flex-col gap-0.5 min-w-0 flex-1">
              <span className="text-[13.5px] font-semibold text-text-primary break-words">{doc.name}</span>
              <span className="text-[12px] text-text-muted">
                {doc.sizeKb} KB - version {doc.version} - {formatDateTime(doc.uploadedAt)}
              </span>
            </div>
            <Badge tone="default">{doc.source === 'uploaded' ? 'Uploaded by PO' : doc.integrationName ?? 'Integration'}</Badge>
          </div>

          <div className="bg-surface-tertiary rounded-lg p-5 max-h-[55vh] overflow-y-auto">
            <DocumentPreview name={doc.name} />
          </div>

          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-text-faint flex items-center gap-1.5">
              <Icon name="info" size={12} />
              Illustrative preview generated for this prototype - not the vendor's actual document.
            </span>
            <div className="flex gap-2 shrink-0">
              <Button variant="secondary" icon={<Icon name="download" size={14} />} onClick={() => toast('Downloaded ' + doc.name + '.', 'success')}>
                Download
              </Button>
              <Button variant="primary" onClick={onClose}>
                Close
              </Button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
