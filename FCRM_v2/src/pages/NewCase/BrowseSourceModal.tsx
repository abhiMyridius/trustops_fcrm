import { useEffect, useState } from 'react';
import type { FileRef, Integration } from '@/types';
import { getIntegrationBrowseFiles } from '@/services/api';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Checkbox } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';

/** The stated effect of connecting a document source, made real (§8.9 mod, §4) - intake offers
 * browsing the connected source alongside manual upload, instead of only describing it. */
export function BrowseSourceModal({ integration, onClose, onImport }: { integration: Integration | null; onClose: () => void; onImport: (files: FileRef[]) => void }) {
  const [files, setFiles] = useState<FileRef[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    if (!integration) return;
    getIntegrationBrowseFiles(integration.id).then(setFiles);
  }, [integration]);

  if (!integration) return null;

  function toggle(name: string) {
    setPicked((p) => (p.includes(name) ? p.filter((n) => n !== name) : [...p, name]));
  }

  async function confirmImport() {
    if (!files) return;
    setImporting(true);
    const selected = files.filter((f) => picked.includes(f.name));
    await onImport(selected);
    setImporting(false);
  }

  return (
    <Modal open onClose={onClose} title={`Browse ${integration.name}`} width={480}>
      <div className="flex flex-col gap-4">
        <p className="text-[12.5px] text-text-muted">
          Scoped to {integration.scope?.sites?.join(', ') || 'your connected sites'}. Pick what's relevant - the Extraction Agent reads it the same as a manual upload.
        </p>
        {!files ? (
          <Skeleton className="h-24 rounded-md" />
        ) : (
          <div className="flex flex-col gap-1.5 border border-border-strong rounded-md p-2.5 max-h-64 overflow-y-auto">
            {files.map((f) => (
              <label key={f.name} className="flex items-center gap-2.5 px-2 py-2 rounded-md hover:bg-surface-tertiary cursor-pointer">
                <Checkbox checked={picked.includes(f.name)} onChange={() => toggle(f.name)} />
                <Icon name="document" size={15} className="text-text-muted shrink-0" />
                <span className="flex-1 text-[13px] text-text-primary truncate">{f.name}</span>
                <span className="text-[11.5px] text-text-faint shrink-0">{f.sizeKb} KB</span>
              </label>
            ))}
            {files.length === 0 && <p className="text-[13px] text-text-faint px-2 py-3">No documents found in the current scope.</p>}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={confirmImport} loading={importing} disabled={picked.length === 0}>
            Import {picked.length > 0 ? picked.length : ''} file{picked.length === 1 ? '' : 's'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
