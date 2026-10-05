/** 2.10 PO Integrations: connector rows; the connect wizard collects location/scope only, never a secret. */
import { useState } from 'react';
import { Database, FileStack, MessagesSquare } from 'lucide-react';
import { useIntegrationAction, useIntegrations } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import type { Integration } from '@/api/types/po';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { QueryState } from '@/components/ui/QueryState';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';

const ICON = { document_source: FileStack, notification_channel: MessagesSquare, data_catalogue: Database };
const KIND = { document_source: 'Document source', notification_channel: 'Notification channel', data_catalogue: 'Data catalogue' };

export function Integrations() {
  useDocumentTitle('Integrations');
  const q = useIntegrations();
  const action = useIntegrationAction();
  const toast = useToast();
  const [connecting, setConnecting] = useState<Integration | null>(null);
  const [disconnecting, setDisconnecting] = useState<Integration | null>(null);
  const [scope, setScope] = useState('');

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Integrations" subtitle="Where documents come from and where notices go." />
      <Banner tone="rule">No password, token or secret is collected or stored. Connections use the bank&apos;s directory permissions.</Banner>
      <QueryState query={q} label="Loading integrations">
        {(rows) => (
          <ul className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {rows.map((r) => {
              const Icon = ICON[r.kind];
              const connected = r.status !== 'not_connected';
              return (
                <li key={r.id} className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-5 shadow-sm">
                  <div className="flex items-start gap-3">
                    <Icon aria-hidden size={22} className="mt-0.5 text-digital-pavo" />
                    <div className="min-w-0 flex-1">
                      <h2 className="text-sm font-bold">{r.name}</h2>
                      <p className="text-[12px] text-text-muted">{KIND[r.kind]}</p>
                    </div>
                    <Badge tone={connected ? 'success' : 'neutral'}>{r.status === 'connected_demo' ? 'Connected (demo)' : connected ? 'Connected' : 'Not connected'}</Badge>
                  </div>
                  <p className="text-[13px] text-text-secondary">{r.description}</p>
                  {r.scope && <p className="text-[12px] text-text-muted">Scope: {r.scope}</p>}
                  <div>
                    {connected ? (
                      <Button size="sm" variant="ghost" onClick={() => setDisconnecting(r)}>
                        Disconnect…
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          setScope('');
                          setConnecting(r);
                        }}
                      >
                        Connect…
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </QueryState>

      <Modal
        open={connecting !== null}
        onClose={() => setConnecting(null)}
        title={`Connect ${connecting?.name ?? ''}`}
        description="Location and scope only. No password, token or secret is collected or stored."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConnecting(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!scope.trim()}
              loading={action.isPending}
              onClick={() =>
                connecting &&
                action.mutate(
                  { id: connecting.id, action: 'connect', scope },
                  {
                    onSuccess: () => {
                      toast.success(`${connecting.name} connected`);
                      setConnecting(null);
                    },
                    onError: (err) => toast.error('Not connected', userMessage(err)),
                  },
                )
              }
            >
              Connect
            </Button>
          </>
        }
      >
        <Input
          label={connecting?.kind === 'notification_channel' ? 'Channel' : connecting?.kind === 'data_catalogue' ? 'Catalogue domain' : 'Library or folder'}
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          placeholder={connecting?.kind === 'notification_channel' ? '#payments-fcrm' : 'Payments / Change requests'}
          data-autofocus
        />
      </Modal>

      <Modal
        open={disconnecting !== null}
        onClose={() => setDisconnecting(null)}
        title={`Disconnect ${disconnecting?.name ?? ''}?`}
        description={disconnecting?.disconnect_consequence}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDisconnecting(null)}>
              Keep connected
            </Button>
            <Button
              variant="danger"
              loading={action.isPending}
              onClick={() =>
                disconnecting &&
                action.mutate(
                  { id: disconnecting.id, action: 'disconnect' },
                  {
                    onSuccess: () => {
                      toast.success(`${disconnecting.name} disconnected`);
                      setDisconnecting(null);
                    },
                    onError: (err) => toast.error('Not disconnected', userMessage(err)),
                  },
                )
              }
            >
              Disconnect
            </Button>
          </>
        }
      >
        <p className="text-sm">Nothing already in a case is removed.</p>
      </Modal>
    </div>
  );
}
