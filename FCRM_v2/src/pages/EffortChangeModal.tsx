import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import type { AgentConfig } from '@/types';

const EFFORT_ORDER: Record<AgentConfig['effort'], number> = { Low: 0, Standard: 1, High: 2 };

export function effortDirection(from: AgentConfig['effort'], to: AgentConfig['effort']): 'up' | 'down' | 'same' {
  const diff = EFFORT_ORDER[to] - EFFORT_ORDER[from];
  if (diff > 0) return 'up';
  if (diff < 0) return 'down';
  return 'same';
}

export function EffortChangeModal({
  open,
  agentName,
  fromEffort,
  toEffort,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  agentName: string;
  fromEffort: AgentConfig['effort'];
  toEffort: AgentConfig['effort'];
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const direction = effortDirection(fromEffort, toEffort);
  const increasing = direction === 'up';

  return (
    <Modal open={open} onClose={onCancel} title={increasing ? 'Higher effort selected' : 'Lower effort selected'} width={440}>
      <div className="flex flex-col gap-4">
        <div className={`flex items-start gap-2.5 rounded-md px-3.5 py-3 ${increasing ? 'bg-danger-bg' : 'bg-verdant-1'}`}>
          <Icon name={increasing ? 'warning' : 'info'} size={16} className={increasing ? 'text-danger shrink-0 mt-0.5' : 'text-teal shrink-0 mt-0.5'} />
          <p className={`text-[13px] leading-relaxed ${increasing ? 'text-danger' : 'text-[#133338]'}`}>
            {increasing ? (
              <>
                Moving the <strong>{agentName} Agent</strong> from {fromEffort} to {toEffort} effort will make it slower and more expensive to run, on every case
                going forward, not just this one. Use higher effort where the extra thoroughness is worth the cost.
              </>
            ) : (
              <>
                Moving the <strong>{agentName} Agent</strong> from {fromEffort} to {toEffort} effort will make it faster and cheaper to run, on every case going
                forward. Potential savings: lower cost per run and quicker turnaround, with somewhat less thoroughness on ambiguous cases.
              </>
            )}
          </p>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant={increasing ? 'primary' : 'accent'} onClick={onConfirm}>
            {increasing ? 'Confirm increase' : 'Confirm and save'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
