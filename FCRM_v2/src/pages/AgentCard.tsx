import { useState } from 'react';
import type { AgentConfig, ModelInfo } from '@/types';
import { Card } from '@/components/ui/Card';
import { Select } from '@/components/ui/Form';
import { Button } from '@/components/ui/Button';
import { Icon, type IconName } from '@/components/ui/Icon';
import { EffortChangeModal, effortDirection } from './EffortChangeModal';

export function AgentCard({
  config,
  description,
  icon,
  models,
  onSave,
}: {
  config: AgentConfig;
  description: string;
  icon: IconName;
  models: ModelInfo[];
  onSave: (patch: Partial<AgentConfig>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draftModel, setDraftModel] = useState(config.modelId);
  const [draftEffort, setDraftEffort] = useState(config.effort);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const modelName = models.find((m) => m.id === config.modelId)?.name ?? config.modelId;

  function startEdit() {
    setDraftModel(config.modelId);
    setDraftEffort(config.effort);
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
  }

  async function applyChange() {
    setSaving(true);
    await onSave({ modelId: draftModel, effort: draftEffort });
    setSaving(false);
    setEditing(false);
    setConfirmOpen(false);
  }

  function handleSaveClick() {
    if (draftModel === config.modelId && draftEffort === config.effort) {
      setEditing(false);
      return;
    }
    if (effortDirection(config.effort, draftEffort) !== 'same') {
      setConfirmOpen(true);
    } else {
      applyChange();
    }
  }

  return (
    <Card material="flat" className="p-5 flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 rounded-lg bg-carbon-moss-deep flex items-center justify-center shrink-0">
            <Icon name={icon} size={18} className="text-quantum-leaf" />
          </div>
          <div className="flex flex-col gap-0.5 min-w-0">
            <span className="text-[14px] font-semibold text-text-primary">{config.agent} Agent</span>
            <span className="text-[12px] text-text-muted leading-snug">{description}</span>
          </div>
        </div>
        {!editing && (
          <button onClick={startEdit} className="shrink-0 text-text-faint hover:text-carbon-moss hover:bg-surface-tertiary p-1.5 rounded-md cursor-pointer" title="Edit model and effort">
            <Icon name="edit" size={15} />
          </button>
        )}
      </div>

      {!editing ? (
        <div className="grid grid-cols-2 gap-3 pt-1 border-t border-border-hairline mt-1">
          <div className="flex flex-col gap-0.5 pt-3">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Model</span>
            <span className="text-[13.5px] font-semibold text-text-primary">{modelName}</span>
          </div>
          <div className="flex flex-col gap-0.5 pt-3">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Effort</span>
            <span className="text-[13.5px] font-semibold text-text-primary">{config.effort}</span>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3 pt-1 border-t border-border-hairline mt-1">
          <div className="grid grid-cols-2 gap-3 pt-3">
            <div className="flex flex-col gap-1">
              <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint px-0.5">Model</label>
              <Select value={draftModel} onChange={(e) => setDraftModel(e.target.value)} className="text-[13px]">
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint px-0.5">Effort</label>
              <Select value={draftEffort} onChange={(e) => setDraftEffort(e.target.value as AgentConfig['effort'])} className="text-[13px]">
                <option value="Low">Low</option>
                <option value="Standard">Standard</option>
                <option value="High">High</option>
              </Select>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={cancelEdit}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={handleSaveClick} loading={saving}>
              Save
            </Button>
          </div>
        </div>
      )}

      <EffortChangeModal
        open={confirmOpen}
        agentName={config.agent}
        fromEffort={config.effort}
        toEffort={draftEffort}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={applyChange}
      />
    </Card>
  );
}
