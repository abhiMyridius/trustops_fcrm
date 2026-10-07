/** 2.23 Assurance › Agent quality: per-agent eval results for the week, regressions and drift. */
import { useSearchParams } from 'react-router-dom';
import { ApiError } from '@/api/errors';
import { useAgentQuality } from '@/api/queries/assuranceQueries';
import type { AgentQualityRow } from '@/api/types/config';
import { ChartCard } from '@/components/charts/ChartCard';
import { SimpleBarChart } from '@/components/charts/Charts';
import { CHART } from '@/components/charts/palette';
import { Badge } from '@/components/ui/Badge';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Table, type Column } from '@/components/ui/Table';
import { useDocumentTitle } from '@/lib/a11y';
import { formatInt } from '@/lib/format';
import { weekParam } from '@/lib/isoWeek';

/** One line for the chart card, read off the rows: the lowest higher-is-better result, and what the lower-is-better rows are. */
function insightOf(rows: AgentQualityRow[]): string {
  const higher = rows.filter((r) => !r.lower_is_better);
  const lowest = higher.reduce<AgentQualityRow | undefined>((low, r) => (low === undefined || r.pct < low.pct ? r : low), undefined);
  const rates = rows.filter((r) => r.lower_is_better).map((r) => r.agent);
  const first = lowest ? `${lowest.agent} ${lowest.metric} (${lowest.pct.toFixed(1)} %) is the lowest` : 'No agent results this week';
  return rates.length > 0 ? `${first}; ${rates.join(', ')} ${rates.length === 1 ? 'is a rate where lower is better' : 'are rates where lower is better'}.` : `${first}.`;
}

export function AgentQualityTab() {
  useDocumentTitle('Agent quality', 'Assurance');
  const [params] = useSearchParams();
  // S4 takes an ISO week (`2026-W40`) and answers the newest published week when none is sent.
  const q = useAgentQuality(weekParam(params.get('week')));
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
  // 404 is not a fault: no evaluation run is published for the week yet.
  if (q.error instanceof ApiError && q.error.status === 404) {
    return <EmptyState title="No evaluation run is published for this week yet" body="Results appear here once an evaluation run is published." />;
  }
  return (
    <QueryState query={q} label="Loading agent quality">
      {(d) => (
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone={d.regressions === 0 ? 'success' : 'danger'}>
              {d.regressions} regressions vs the previous week
            </Badge>
            <span className="text-text-secondary">
              Week {d.week} · {d.golden_cases} golden cases · drift: {d.drift}
            </span>
            <a href="/evals/results/latest.html" className="font-semibold">
              Full eval report
            </a>
          </div>
          <ChartCard title="Eval result by agent" insight={insightOf(d.rows)} data={d.rows.map((r) => ({ label: r.agent, value: `${r.pct} %` }))} height={280}>
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
