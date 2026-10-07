import type { AgentsData, ConfigSectionBase } from '@/api/types/config';
import { AiLabel } from '@/components/ui/AiMark';
import { Badge, Tag } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Card } from '@/components/ui/Card';
import { DraftEdit } from '../DraftEdit';

/**
 * 8. AI Agents: nine agents with tier, model → fallback, tools allow-list, skills, output, gate,
 * budget, eval set and last eval score. Model names are the seed's aliases; live values come from
 * ai/models/tiers.yaml (the only file that names model ids, ADR-04).
 */
export function AIAgents({ s }: { s: ConfigSectionBase<'ai-agents', AgentsData> }) {
  return (
    <div className="flex flex-col gap-5">
      <Banner tone="warning">A model change must pass the agent&apos;s eval set before it is used on cases.</Banner>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {s.data.agents.map((a) => (
          <Card
            key={a.name}
            title={<AiLabel>{a.name}</AiLabel>}
            headingLevel={3}
            subtitle={`${a.tier} tier · ${a.budget}`}
            actions={<DraftEdit section={s.section} etag={s.draft.etag} path={`${a.name}.tier`} label={`${a.name} tier`} current={a.tier} />}
          >
            <dl className="flex flex-col gap-1.5 text-[13px]">
              <div>
                <dt className="inline font-semibold">Model → fallback: </dt>
                <dd className="inline font-mono text-[12px]">
                  {a.model} → {a.fallback}
                </dd>
              </div>
              <div>
                <dt className="inline font-semibold">Output: </dt>
                <dd className="inline">{a.output}</dd>
              </div>
              <div>
                <dt className="inline font-semibold">Human gate: </dt>
                <dd className="inline">{a.gate}</dd>
              </div>
              <div>
                <dt className="inline font-semibold">Eval set: </dt>
                <dd className="inline">
                  {a.eval_set} · <Badge tone="success">{a.eval_score.toFixed(1)} %</Badge> <span className="text-text-muted">({a.eval_metric})</span>
                </dd>
              </div>
            </dl>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {a.tools.map((t) => (
                <Tag key={t}>{t}</Tag>
              ))}
              {a.skills.map((k) => (
                <Badge key={k} tone="info">
                  skill: {k}
                </Badge>
              ))}
            </div>
          </Card>
        ))}
      </div>
      <p className="text-[12px] text-text-muted">
        Per-case budget {Math.round(s.data.per_case_budget / 1000)}k tokens. Pack generation and scoring are not listed here because they are deterministic.
      </p>
    </div>
  );
}
