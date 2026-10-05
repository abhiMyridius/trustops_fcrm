import { useState } from 'react';
import type { Integration } from '@/types';
import { disconnectIntegration } from '@/services/api';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';

const CONSEQUENCE: Record<Integration['category'], (i: Integration) => string> = {
  'Document sources': (i) => `The Extraction Agent will no longer pull documents from ${i.name}. Anything already imported stays on cases where it was used.`,
  'Communication apps': (i) => `${i.name} will be removed from your notification channels in Settings. Notifications will still appear in the bell.`,
  'MCP servers / apps': (i) => `${i.name}'s tools will no longer be available to the intake agents.`,
};

/** Disconnect confirmation naming what stops working (§8.9 mod, §3) - specific to the
 * integration's category, not a generic "are you sure?". */
export function IntegrationDisconnectModal({ integration, onClose, onDisconnected }: { integration: Integration | null; onClose: () => void; onDisconnected: () => void }) {
  const [disconnecting, setDisconnecting] = useState(false);
  if (!integration) return null;
  const current = integration;

  async function confirm() {
    setDisconnecting(true);
    await disconnectIntegration(current.id);
    setDisconnecting(false);
    onDisconnected();
    onClose();
  }

  return (
    <Modal open onClose={onClose} title={`Disconnect ${integration.name}?`} width={440}>
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-2.5 bg-danger-bg rounded-md px-3.5 py-3">
          <Icon name="warning" size={16} className="text-danger shrink-0 mt-0.5" />
          <p className="text-[13px] text-danger leading-relaxed">{CONSEQUENCE[integration.category](integration)}</p>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={confirm} loading={disconnecting}>
            Disconnect
          </Button>
        </div>
      </div>
    </Modal>
  );
}
