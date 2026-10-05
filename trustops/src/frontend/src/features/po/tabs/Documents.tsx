/** 2.4 PO Documents: upload (or the demo's two files), Guard 1 line per file, assistant status line. */
import { useEffect, useRef, useState, type DragEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { CloudUpload, FileText, Loader2, Sparkles } from 'lucide-react';
import { useDocuments, useUploadDocuments } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import { CompletionRing } from '@/components/domain/CompletionRing';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { formatDateTime } from '@/lib/format';

const DEMO_FILES = [
  { name: 'business_case_remittance_MX.pdf', size: 2_100_000 },
  { name: 'payout_partner_contract.pdf', size: 800_000 },
];
const MAX_BYTES = 25 * 1024 * 1024;

export function DocumentsTab() {
  const { id = '' } = useParams();
  useDocumentTitle('Documents', id);
  const q = useDocuments(id);
  const upload = useUploadDocuments(id);
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const qc = useQueryClient();
  const assistantState = q.data?.assistant.state;
  const prevState = useRef(assistantState);
  // When reading finishes, the case header, Overview and Review all change: refresh the case subtree.
  useEffect(() => {
    if (prevState.current === 'reading' && assistantState === 'done') void qc.invalidateQueries({ queryKey: ['case', id] });
    prevState.current = assistantState;
  }, [assistantState, id, qc]);

  async function send(files: File[]) {
    const tooBig = files.find((f) => f.size > MAX_BYTES);
    if (tooBig) {
      toast.error('File too large', `${tooBig.name} is over 25 MB.`);
      return;
    }
    const form = new FormData();
    files.forEach((f) => form.append('files', f));
    try {
      await upload.mutateAsync(form);
      toast.success(`${files.length} file${files.length > 1 ? 's' : ''} uploaded`, 'Checked for hidden instructions and personal data before any AI reads them.');
    } catch (err) {
      toast.error('Upload failed', userMessage(err));
    }
  }

  async function sendDemo() {
    try {
      await upload.mutateAsync(DEMO_FILES);
      toast.success('2 demo files uploaded');
    } catch (err) {
      toast.error('Upload failed', userMessage(err));
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) void send(files);
  }

  return (
    <QueryState query={q} label="Loading documents">
      {(v) => (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_320px]">
          <div className="flex flex-col gap-5">
            <Card title="Documents" subtitle="PDF, Word, Excel, PowerPoint, CSV or text · 25 MB per file">
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                className={`flex flex-col items-center gap-3 rounded-lg border-2 border-dashed px-6 py-8 text-center ${dragging ? 'border-teal bg-success-bg' : 'border-border-strong bg-surface-tertiary'}`}
              >
                <CloudUpload aria-hidden size={28} className="text-text-muted" />
                <p className="text-sm font-semibold">Drop files here, or browse</p>
                <div className="flex flex-wrap justify-center gap-2">
                  <input
                    ref={input}
                    type="file"
                    multiple
                    className="sr-only"
                    aria-label="Choose files to upload"
                    accept=".pdf,.docx,.xlsx,.pptx,.csv,.md,.txt"
                    onChange={(e) => {
                      const files = Array.from(e.target.files ?? []);
                      if (files.length) void send(files);
                      e.target.value = '';
                    }}
                  />
                  <Button variant="secondary" onClick={() => input.current?.click()} loading={upload.isPending}>
                    Browse files
                  </Button>
                  {id === 'FCRM-2026-0151' && v.documents.length === 0 && (
                    <Button variant="accent" onClick={() => void sendDemo()} loading={upload.isPending}>
                      Use the demo's two files
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    disabled={!v.sharepoint_connected}
                    title={v.sharepoint_connected ? undefined : 'Connect in Integrations'}
                    onClick={() => toast.info('SharePoint import', 'Demo connector: choose files with Browse; the S3/MinIO source stands in (ADR-17).')}
                  >
                    Import from SharePoint
                  </Button>
                </div>
                {!v.sharepoint_connected && (
                  <p className="text-[12px] text-text-muted">
                    SharePoint is not connected. <Link to="/integrations">Connect in Integrations</Link>
                  </p>
                )}
              </div>
              <Banner tone="rule" className="mt-4">
                Before any AI reads a file, it is checked for hidden instructions and personal data is masked.
              </Banner>
            </Card>

            {v.documents.length === 0 ? (
              <EmptyState title="No documents yet" body="Upload what you already have. You can add more later; nothing is submitted until you choose to." />
            ) : (
              <ul className="flex flex-col gap-2" aria-label="Uploaded documents">
                {v.documents.map((d) => (
                  <li key={d.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3 shadow-sm">
                    <FileText aria-hidden size={20} className="text-digital-pavo" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold">{d.name}</p>
                      <p className="text-[12px] text-text-muted">
                        {d.pages} pages · {d.size_mb.toFixed(1)} MB · uploaded {formatDateTime(d.uploaded_at)}
                        {d.added_in ? ` · added in ${d.added_in}` : ''}
                      </p>
                      {d.guard_line && d.status !== 'stored' && <p className="mt-0.5 text-[12px] text-band-1-text">{d.guard_line}</p>}
                    </div>
                    <Badge tone={d.status === 'read' ? 'success' : 'info'}>{{ uploading: 'Uploading', stored: 'Stored', scanned: 'Scanned', read: 'Read' }[d.status]}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex flex-col gap-5">
            <Card title="Assistant" headingLevel={2}>
              <p className="flex items-start gap-2 text-sm" role="status" aria-live="polite">
                {v.assistant.state === 'reading' ? (
                  <Loader2 aria-hidden size={16} className="mt-0.5 shrink-0 animate-spin text-teal" />
                ) : (
                  <Sparkles aria-hidden size={16} className="mt-0.5 shrink-0 text-teal" />
                )}
                <span>{v.assistant.line}</span>
              </p>
              {v.assistant.state === 'done' && v.documents.length > 0 && (
                <Link to={`/case/${id}/overview`} className="mt-3 inline-block text-sm font-semibold">
                  Review on the Overview tab
                </Link>
              )}
            </Card>
            <Card title="Required fields">
              <CompletionRing completion={v.completion} />
            </Card>
          </div>
        </div>
      )}
    </QueryState>
  );
}
