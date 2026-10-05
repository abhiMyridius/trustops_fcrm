/** 2.23 Assurance › Agent quality: per-agent eval results for the week, regressions and drift. */
import { useSearchParams } from 'react-router-dom';
import { useAgentQuality } from '@/api/queries/assuranceQueries';
import type { AgentQualityRow } from '@/api/types/config';
import { ChartCard } from '@/components/charts/ChartCard';
import { SimpleBarChart } from '@/components/charts/Charts';
import { CHART } from '@/components/charts/palette';
import { Badge } from '@/components/ui/Badge';
import { QueryState } from '@/components/ui/QueryState';
import { Table, type Column } from '@/components/ui/Table';
import { useDocumentTitle } from '@/lib/a11y';
import { formatDate, formatInt } from '@/lib/format';

export function AgentQualityTab() {
  useDocumentTitle('Agent quality', 'Assurance');
  const [params] = useSearchParams();
  const q = useAgentQuality(params.get('week') ?? '2026-09-28');
  const columns: Column<AgentQualityRow>[] = [
    { key: 'agent', header: 'Agent', sortValue: (r) => r.agent, cell: (r) => <span className="font-semibold">{r.agent}</span> },
    { key: 'metric', header: 'Metric', cell: (r) => r.metric },
    { key: 'n', header: 'n', align: 'right', sortValue: (r) => r.n, cell: (r) => formatInt(r.n) },
    {
      key: 'pct',
      header: 'Result',
      align: 'right',
      sortValue: (r) => r.pct,
      cell: (r) => (
        <span className="font-bold">
          {r.pct.toFixed(1)} %{r.lower_is_better && <span className="ml-1 text-[11px] font-normal text-text-muted">(lower is better)</span>}
        </span>
      ),
    },
  ];
  return (
    <QueryState query={q} label="Loading agent quality">
      {(d) => (
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone={d.regressions === 0 ? 'success' : 'danger'}>
              {d.regressions} regressions vs {formatDate('2026-09-21')}
            </Badge>
            <span className="text-text-secondary">
              Week of {formatDate(d.week)} · {d.golden_cases} golden cases · drift: {d.drift}
            </span>
            <a href="/evals/results/latest.html" className="font-semibold">
              Full eval report
            </a>
          </div>
          <ChartCard title="Eval result by agent" insight="policy_gap suggestion acceptance (78.6 %) is the lowest; assessment is an override rate." data={d.rows.map((r) => ({ label: r.agent, value: `${r.pct} %` }))} height={280}>
            <SimpleBarChart
              layout="vertical"
              valueSuffix=" %"
              data={d.rows.map((r) => ({ label: r.agent, value: r.pct, key: r.lower_is_better ? 'lower' : 'higher' }))}
              colorFor={(x) => (x.key === 'lower' ? CHART.highlight : CHART.series[0])}
            />
          </ChartCard>
          <Table caption="Agent quality" columns={columns} rows={d.rows} rowKey={(r) => r.agent} />
        </div>
      )}
    </QueryState>
  );
}
