/** 2.11 Analyst Dashboard: seed-derived KPIs and charts (band distribution 19/49/11/1, eval scores, closures). */
import { Link } from 'react-router-dom';
import { useAnalystDashboard } from '@/api/queries/analystQueries';
import type { QueueItem } from '@/api/types/analyst';
import { ChartCard } from '@/components/charts/ChartCard';
import { SimpleBarChart } from '@/components/charts/Charts';
import { KpiCard } from '@/components/charts/KpiCard';
import { CHART } from '@/components/charts/palette';
import { BandChip } from '@/components/domain/ScoreBadge';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { QueryState } from '@/components/ui/QueryState';
import { Table, type Column } from '@/components/ui/Table';
import { useDocumentTitle } from '@/lib/a11y';
import { caseShortId, formatDate, formatFixed, formatMonth } from '@/lib/format';

export function AnalystDashboard() {
  useDocumentTitle('Analyst dashboard');
  const q = useAnalystDashboard();
  const columns: Column<QueueItem>[] = [
    {
      key: 'case',
      header: 'Case',
      cell: (r) => (
        <Link to={`/analyst/case/${r.id}`} className="font-semibold">
          <span className="font-mono text-[12px] text-text-muted">{caseShortId(r.id)}</span> {r.title}
        </Link>
      ),
    },
    { key: 'stage', header: 'Stage', cell: (r) => <Badge tone={r.is_new ? 'warning' : 'info'}>{r.stage_label}</Badge> },
    { key: 'due', header: 'Due', cell: (r) => formatDate(r.due) },
    { key: 'band', header: 'Band', cell: (r) => (r.band ? <BandChip band={r.band} label={r.band_label} /> : <span className="text-text-muted">withheld</span>) },
  ];
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Analyst dashboard" subtitle="What is assigned to you, and how the portfolio and the agents are doing. Counts and averages are computed from the stored cases and scores; reference tables come from the seed." />
      <QueryState query={q} label="Loading dashboard">
        {(d) => (
          <>
            <section aria-label="Key figures" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <KpiCard label="Open, assigned to me" value={d.kpis.open_assigned} />
              <KpiCard label="Approaching due" value={d.kpis.approaching_due} hint="Due within a business day" tone={d.kpis.approaching_due ? 'attention' : 'default'} />
              <KpiCard label="Avg intake to decision" value={formatFixed(d.kpis.avg_days_intake_to_decision)} unit="days" />
              <KpiCard label="Closed cases (2026)" value={d.kpis.closed_cases} />
            </section>
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <ChartCard
                title="Band distribution · 80 seeded cases"
                insight="Most changes land in Band 2; 12 of 80 reached the Risk Committee."
                data={d.band_distribution.map((b) => ({ label: `Band ${b.band} ${b.label}`, value: b.count }))}
              >
                <SimpleBarChart
                  data={d.band_distribution.map((b) => ({ label: `Band ${b.band}`, value: b.count, key: b.band }))}
                  colorFor={(x) => CHART.band[Number(x.key)] ?? CHART.series[0]}
                />
              </ChartCard>
              <ChartCard
                title={`Agent eval scores · week of ${formatDate('2026-09-28')}`}
                insight="Assessment shows its override rate (lower is better): 17.9 % of proposals were overridden."
                data={d.agent_eval.map((a) => ({ label: `${a.agent} (${a.metric})`, value: `${a.pct} %` }))}
                height={280}
              >
                <SimpleBarChart
                  layout="vertical"
                  valueSuffix=" %"
                  data={d.agent_eval.map((a) => ({ label: a.agent, value: a.pct, key: a.lower_is_better ? 'lower' : 'higher' }))}
                  colorFor={(x) => (x.key === 'lower' ? CHART.highlight : CHART.series[1])}
                />
              </ChartCard>
              <ChartCard
                title="Cases decided per month"
                insight="Closures by decision month across the seeded portfolio."
                data={d.closed_by_month.map((m) => ({ label: formatMonth(`${m.month}-01`), value: m.count }))}
              >
                <SimpleBarChart data={d.closed_by_month.map((m) => ({ label: formatMonth(`${m.month}-01`).slice(0, 3), value: m.count }))} />
              </ChartCard>
              <ChartCard
                title="Control inventory health"
                insight={`${d.control_health.stale} of ${d.control_health.total} controls have evidence older than ${d.control_health.recency_months} months (${d.control_health.stale_ids.join(', ') || 'none'}).`}
                data={[
                  { label: 'Current evidence', value: d.control_health.healthy },
                  { label: 'Stale evidence', value: d.control_health.stale },
                ]}
                height={160}
              >
                <SimpleBarChart
                  layout="vertical"
                  data={[
                    { label: 'Current', value: d.control_health.healthy, key: 'ok' },
                    { label: 'Stale', value: d.control_health.stale, key: 'stale' },
                  ]}
                  colorFor={(x) => (x.key === 'stale' ? CHART.highlight : CHART.series[1])}
                />
              </ChartCard>
            </div>
            <section aria-labelledby="mine" className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <h2 id="mine" className="text-base font-bold">
                  My queue · {d.mine.length} cases
                </h2>
                <Link to="/analyst/queue" className="text-sm font-semibold">
                  Open queue
                </Link>
              </div>
              <Table caption="Cases assigned to me" columns={columns} rows={d.mine} rowKey={(r) => r.id} empty="Nothing waiting on you." />
            </section>
          </>
        )}
      </QueryState>
    </div>
  );
}
