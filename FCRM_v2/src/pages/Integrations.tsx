import { useEffect, useState } from 'react';
import type { Integration } from '@/types';
import { getIntegrations, setIntegrationEnabled } from '@/services/api';
import { Card } from '@/components/ui/Card';
import { Switch } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { formatDateTime } from '@/utils/format';
import { useToast } from '@/components/ui/Toast';
import { IntegrationConnectModal } from './IntegrationConnectModal';
import { IntegrationDisconnectModal } from './IntegrationDisconnectModal';

const CATEGORIES: Integration['category'][] = ['Document sources', 'Communication apps', 'MCP servers / apps'];

const CATEGORY_DESC: Record<Integration['category'], string> = {
  'Document sources': 'Where the Extraction Agent can pull business cases, contracts, and diagrams from directly.',
  'Communication apps': 'Feeds the channel options in your notification preferences - only connected apps appear there.',
  'MCP servers / apps': 'Tools and data sources you connect for your own intake workflow.',
};

// Stated on the page so the consequence of connecting is visible before the PO does it
// (§8.9 mod, §4) - and its mirror, since disconnecting reverses each.
const CATEGORY_EFFECT: Record<Integration['category'], string> = {
  'Document sources': 'Once connected: intake chat offers browsing this source alongside manual upload.',
  'Communication apps': 'Once connected: becomes selectable as a channel in Settings notification preferences.',
  'MCP servers / apps': 'Once connected: its enabled tools become available to the intake agents.',
};

function ScopeSummary({ i }: { i: Integration }) {
  if (i.category === 'Document sources') {
    return (
      <span>
        Scoped to {i.scope?.sites?.join(', ') || 'no sites yet'}
        {i.scope?.documentCount != null && ` · ${i.scope.documentCount} documents in scope`}
      </span>
    );
  }
  if (i.category === 'Communication apps') {
    return <span>Sends to {i.scope?.destination || 'no destination set'}</span>;
  }
  const enabledTools = i.scope?.tools?.filter((t) => t.enabled).map((t) => t.name) ?? [];
  return <span>Tools: {enabledTools.length > 0 ? enabledTools.join(', ') : 'none enabled'}</span>;
}

export function Integrations() {
  const [items, setItems] = useState<Integration[] | null>(null);
  const [connectTarget, setConnectTarget] = useState<Integration | null>(null);
  const [disconnectTarget, setDisconnectTarget] = useState<Integration | null>(null);
  const toast = useToast();

  async function reload() {
    setItems(await getIntegrations());
  }

  useEffect(() => {
    reload();
  }, []);

  async function handleEnabledToggle(i: Integration) {
    await setIntegrationEnabled(i.id, !i.enabled);
    toast(`${i.name} ${i.enabled ? 'paused' : 'resumed'}.`, 'default');
    reload();
  }

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-[26px] font-bold text-text-primary">Integrations</h1>
        <p className="text-[13.5px] text-text-muted mt-1">What you connect for your own workflow - deliberately minimal. Everything starts disconnected until you connect it.</p>
      </div>

      {!items && (
        <div className="flex flex-col gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 rounded-md" />
          ))}
        </div>
      )}

      {items &&
        CATEGORIES.map((cat) => (
          <div key={cat} className="flex flex-col gap-2.5">
            <div>
              <span className="text-sm font-bold text-text-primary">{cat}</span>
              <p className="text-[12.5px] text-text-muted mt-0.5">{CATEGORY_DESC[cat]}</p>
              <p className="text-[11.5px] text-teal mt-0.5">{CATEGORY_EFFECT[cat]}</p>
            </div>
            <Card material="flat" className="divide-y divide-border-hairline">
              {items
                .filter((i) => i.category === cat)
                .map((i) => (
                  <div key={i.id} className="flex items-start justify-between gap-4 px-5 py-4">
                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[14px] font-semibold text-text-primary">{i.name}</span>
                        <Badge tone={i.connected ? (i.enabled ? 'success' : 'warning') : 'default'}>
                          {i.connected ? (i.enabled ? 'Connected' : 'Connected · paused') : 'Not connected'}
                        </Badge>
                        {i.connected && i.lastTest && !i.lastTest.ok && <Badge tone="danger">Test failed</Badge>}
                      </div>
                      <span className="text-[12.5px] text-text-muted">{i.description}</span>
                      {i.connected ? (
                        <div className="flex flex-col gap-0.5 mt-1 text-[12px] text-text-secondary">
                          <ScopeSummary i={i} />
                          {i.connectedAt && <span className="text-text-faint">Connected {formatDateTime(i.connectedAt)}</span>}
                          {i.lastTest && !i.lastTest.ok && (
                            <span className="text-danger flex items-center gap-1">
                              <Icon name="warning" size={11} /> Last test failed: {i.lastTest.detail}
                            </span>
                          )}
                        </div>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      {i.connected ? (
                        <>
                          <Switch checked={i.enabled} onChange={() => handleEnabledToggle(i)} label={<span className="text-[12px] text-text-muted">Enabled</span>} />
                          <Button size="sm" variant="secondary" onClick={() => setConnectTarget(i)}>
                            Manage
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setDisconnectTarget(i)}>
                            Disconnect
                          </Button>
                        </>
                      ) : (
                        <Button size="sm" variant="primary" onClick={() => setConnectTarget(i)}>
                          Connect
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
            </Card>
          </div>
        ))}

      <IntegrationConnectModal key={connectTarget ? `${connectTarget.id}-${connectTarget.connected}` : 'connect-none'} integration={connectTarget} onClose={() => setConnectTarget(null)} onConnected={reload} />
      <IntegrationDisconnectModal key={disconnectTarget?.id ?? 'disconnect-none'} integration={disconnectTarget} onClose={() => setDisconnectTarget(null)} onDisconnected={reload} />
    </div>
  );
}
