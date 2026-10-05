import { useEffect, useState } from 'react';
import type { AgentConfig, ModelInfo } from '@/types';
import { getAgentConfigs, getModelReference, updateAgentConfig } from '@/services/api';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Icon, type IconName } from '@/components/ui/Icon';
import { useToast } from '@/components/ui/Toast';
import { AgentCard } from './AgentCard';

export const AGENT_META: Record<string, { description: string; icon: IconName }> = {
  Screening: { description: 'Decides auto-clear vs. needs-a-case in the screening chat.', icon: 'filter' },
  Intake: { description: 'Drives the conversational Q&A that builds your submission.', icon: 'inbox' },
  Extraction: { description: 'Pulls structured field values from uploads and descriptions.', icon: 'document' },
  Classification: { description: 'Suggests category tags from what you upload or describe.', icon: 'folder' },
  'Submission Validation': { description: 'Runs the submit-time completeness gate.', icon: 'check' },
};

export function AIConfiguration() {
  const [configs, setConfigs] = useState<AgentConfig[] | null>(null);
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const toast = useToast();

  async function reload() {
    setConfigs(await getAgentConfigs());
  }

  useEffect(() => {
    reload();
    getModelReference().then(setModels);
  }, []);

  async function handleSave(agent: AgentConfig['agent'], patch: Partial<AgentConfig>) {
    await updateAgentConfig(agent, patch);
    toast('Agent settings updated.', 'success');
    await reload();
  }

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-[26px] font-bold text-text-primary">AI Configuration</h1>
        <p className="text-[13.5px] text-text-muted mt-1">Model and effort settings only - template ownership rules live under Templates.</p>
      </div>

      <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-snug max-w-2xl">
        <Icon name="info" size={15} className="text-teal shrink-0 mt-0.5" />
        This is a speed-vs-thoroughness preference over your own work - it never touches FCRM-owned decision rules like required fields, classification logic, or completion thresholds.
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-sm font-bold text-text-primary">Model reference</span>
        <Card material="flat" className="overflow-hidden">
          {!models ? (
            <Skeleton className="h-40" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]" style={{ tableLayout: 'fixed', minWidth: 720 }}>
                <colgroup>
                  <col style={{ width: '13%' }} />
                  <col style={{ width: '12%' }} />
                  <col style={{ width: '11%' }} />
                  <col style={{ width: '8%' }} />
                  <col style={{ width: '42%' }} />
                  <col style={{ width: '14%' }} />
                </colgroup>
                <thead>
                  <tr className="bg-[#f0efed] text-[11px] uppercase tracking-wide text-text-muted">
                    <th className="text-left font-semibold px-4 py-2.5">Model</th>
                    <th className="text-left font-semibold px-4 py-2.5">Provider</th>
                    <th className="text-left font-semibold px-4 py-2.5">Speed</th>
                    <th className="text-left font-semibold px-4 py-2.5">Cost</th>
                    <th className="text-left font-semibold px-4 py-2.5">Strengths</th>
                    <th className="text-left font-semibold px-4 py-2.5">Context</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m) => (
                    <tr key={m.id} className="border-t border-border-hairline align-top">
                      <td className="px-4 py-3 font-semibold text-text-primary whitespace-nowrap">{m.name}</td>
                      <td className="px-4 py-3 text-text-secondary whitespace-nowrap">{m.provider}</td>
                      <td className="px-4 py-3 text-text-secondary whitespace-nowrap">{m.relativeSpeed}</td>
                      <td className="px-4 py-3 text-text-secondary whitespace-nowrap">{m.relativeCost}</td>
                      <td className="px-4 py-3 text-text-secondary">{m.strengths}</td>
                      <td className="px-4 py-3 text-text-secondary whitespace-nowrap">{m.contextWindow}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-sm font-bold text-text-primary">Agents</span>
        <p className="text-[12.5px] text-text-muted -mt-1">Click the pencil to change an agent's model or effort level.</p>
        <div className="grid grid-cols-2 gap-4">
          {!configs || !models
            ? Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-[124px] rounded-md" />)
            : configs.map((cfg) => (
                <AgentCard
                  key={cfg.agent}
                  config={cfg}
                  description={AGENT_META[cfg.agent].description}
                  icon={AGENT_META[cfg.agent].icon}
                  models={models}
                  onSave={(patch) => handleSave(cfg.agent, patch)}
                />
              ))}
        </div>
      </div>
    </div>
  );
}
