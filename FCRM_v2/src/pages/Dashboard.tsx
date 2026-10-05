import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Line, LineChart, Pie, PieChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { getDashboard } from '@/services/api';
import type { ClarificationType, DashboardData, DashboardTimePreset, TriggerCategory } from '@/types';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Input } from '@/components/ui/Form';
import { useCountUp } from '@/hooks/useCountUp';
import { Icon } from '@/components/ui/Icon';

// One colour per trigger category, used identically across every chart on the page (§4.1) -
// category mix, the rounds comparison, and the clarification-types axis all read the same
// colour for the same category.
const CATEGORY_COLORS: Record<TriggerCategory, string> = {
  'New Product': '#194247',
  'Vendor / Third-Party Onboarding': '#09A78D',
  'New Feature': '#52E081',
  'New Geography': '#337077',
  'Process Change': '#94DEA5',
  'New Customer Segment': '#B98A1E',
};

// A distinct scale for clarification types - deliberately not the category scale, since
// categories are the axis in that chart, not a series.
const TYPE_COLORS: Record<ClarificationType, string> = {
  'Missing required field': '#8a3324',
  'Insufficient detail': '#B98A1E',
  'Inconsistent figures': '#337077',
  'Missing supporting document': '#9caaab',
  'Scope or category question': '#CBEBBD',
};

const SHORT_CATEGORY: Record<TriggerCategory, string> = {
  'Vendor / Third-Party Onboarding': 'Vendor Onboarding',
  'New Product': 'New Product',
  'New Feature': 'New Feature',
  'Process Change': 'Process Change',
  'New Geography': 'New Geography',
  'New Customer Segment': 'New Segment',
};

const FULL_CATEGORY_BY_SHORT: Record<string, TriggerCategory> = Object.fromEntries(
  Object.entries(SHORT_CATEGORY).map(([full, short]) => [short, full as TriggerCategory]),
);

// Tints an axis tick's own text with that category's colour, so the shared scale (§4.1) carries
// through even on charts whose bars are coloured by series or by type instead of by category.
function CategoryXTick(props: unknown) {
  const { x, y, payload } = props as { x: number; y: number; payload: { value: string } };
  const category = FULL_CATEGORY_BY_SHORT[payload.value];
  const color = category ? CATEGORY_COLORS[category] : '#52787d';
  return (
    <text x={x} y={y} dy={12} textAnchor="middle" fontSize={9.5} fontWeight={600} fill={color}>
      {payload.value}
    </text>
  );
}

function CategoryYTick(props: unknown) {
  const { x, y, payload } = props as { x: number; y: number; payload: { value: string } };
  const category = FULL_CATEGORY_BY_SHORT[payload.value];
  const color = category ? CATEGORY_COLORS[category] : '#285055';
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fontSize={11} fontWeight={600} fill={color}>
      {payload.value}
    </text>
  );
}

function CategoryLegend({ categories }: { categories: TriggerCategory[] }) {
  return (
    <div className="flex flex-wrap gap-x-3.5 gap-y-1.5">
      {categories.map((c) => (
        <span key={c} className="flex items-center gap-1.5 text-[11px] font-medium text-text-secondary">
          <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: CATEGORY_COLORS[c] }} />
          {SHORT_CATEGORY[c]}
        </span>
      ))}
    </div>
  );
}

function TypeLegend({ types }: { types: ClarificationType[] }) {
  return (
    <div className="flex flex-wrap gap-x-3.5 gap-y-1.5">
      {types.map((t) => (
        <span key={t} className="flex items-center gap-1.5 text-[11px] font-medium text-text-secondary">
          <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: TYPE_COLORS[t] }} />
          {t}
        </span>
      ))}
    </div>
  );
}

function KpiCard({ label, value, suffix, secondary, zeroState, onClick }: { label: string; value: number; suffix?: string; secondary?: string; zeroState?: { icon: 'check' | 'inbox'; text: string }; onClick: () => void }) {
  const count = useCountUp(value);
  const showZeroState = value === 0 && !!zeroState;
  return (
    <button onClick={onClick} className="text-left cursor-pointer group">
      <Card material="flat" className="p-5 flex flex-col gap-1.5 group-hover:border-carbon-moss transition-colors h-full">
        <span className="text-[13px] font-semibold text-text-muted">{label}</span>
        <span className="font-sans text-[32px] font-bold text-text-primary leading-none">
          {count}
          {suffix}
        </span>
        {showZeroState ? (
          <span className="text-[12.5px] font-semibold text-teal flex items-center gap-1.5">
            <Icon name={zeroState!.icon} size={13} />
            {zeroState!.text}
          </span>
        ) : (
          secondary && <span className="text-[12.5px] font-semibold text-text-muted">{secondary}</span>
        )}
      </Card>
    </button>
  );
}

function ChartCard({ title, insight, chip, legend, children }: { title: string; insight: string; chip?: string; legend?: React.ReactNode; children: React.ReactNode }) {
  return (
    <Card material="flat" className="p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-text-primary">{title}</span>
        {chip && (
          <span
            title="This chart always shows the trailing six months, so the trend stays readable - it doesn't follow the filter above."
            className="text-[10.5px] font-semibold uppercase tracking-wide text-digital-pavo bg-[#EAF3F3] px-2 py-0.5 rounded-full cursor-help"
          >
            {chip}
          </span>
        )}
      </div>
      <div className="h-[220px]">{children}</div>
      {legend}
      <div className="flex items-start gap-2 bg-[#F4F8F7] rounded-md px-3 py-2.5">
        <Icon name="sparkles" size={14} className="text-teal shrink-0 mt-0.5" />
        <p className="text-[12.5px] text-[#133338] leading-snug">{insight}</p>
      </div>
    </Card>
  );
}

const fadeUp = { hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0 } };

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [preset, setPreset] = useState<DashboardTimePreset>('last-3-months');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    getDashboard().then(setData);
  }, []);

  const insight = (id: string) => data?.insights.find((i) => i.chartId === id)?.insightText ?? '';

  function goToQueue(qs: Record<string, string | string[]>) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(qs)) {
      if (Array.isArray(v)) v.forEach((x) => params.append(k, x));
      else params.set(k, v);
    }
    params.set('from', 'dashboard');
    navigate(`/queue?${params.toString()}`);
  }

  const categoryMixMax = data ? Math.max(...data.categoryMix.map((d) => d.count)) : 0;
  const categoryMixDomainMax = Math.max(5, Math.ceil(categoryMixMax / 5) * 5);

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-[26px] font-bold text-text-primary">Dashboard</h1>
          <p className="text-[13.5px] text-text-muted mt-1">Your submissions, cycle time, and what the analysts most often ask about.</p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <div className="flex items-center gap-2 bg-white border border-border-strong rounded-md p-1">
            {(['this-month', 'last-3-months', 'custom'] as DashboardTimePreset[]).map((p) => (
              <button
                key={p}
                onClick={() => setPreset(p)}
                className={`px-3 py-1.5 rounded text-[12.5px] font-semibold cursor-pointer transition-colors ${preset === p ? 'bg-carbon-moss text-white' : 'text-text-secondary hover:bg-surface-tertiary'}`}
              >
                {p === 'this-month' ? 'This Month' : p === 'last-3-months' ? 'Last 3 Months' : 'Custom'}
              </button>
            ))}
            {preset === 'custom' && (
              <div className="flex items-center gap-1.5 pl-2 border-l border-border-hairline ml-1">
                <Input type="date" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} className="py-1.5 text-[12px] w-[128px]" />
                <span className="text-text-faint text-[12px]">-</span>
                <Input type="date" value={customTo} onChange={(e) => setCustomTo(e.target.value)} className="py-1.5 text-[12px] w-[128px]" />
              </div>
            )}
          </div>
          {preset !== 'last-3-months' && <span className="text-[11px] text-text-faint max-w-[280px] text-right">Charts show full-programme volume and aren't affected by this filter - only the queue drill-downs are.</span>}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        {data ? (
          <>
            <KpiCard label="Active cases" value={data.kpis.activeCases} onClick={() => goToQueue({ status: ['Submitted', 'Under Assessment', 'Committee Review'] })} />
            <KpiCard
              label="Needs your response"
              value={data.kpis.needsYourResponse}
              zeroState={{ icon: 'check', text: 'Nothing waiting on you' }}
              onClick={() => goToQueue({ needsResponse: '1' })}
            />
            <KpiCard
              label="Average cycle time"
              value={data.kpis.avgCycleTimeDays}
              suffix=" days"
              secondary={`${Math.abs(data.kpis.avgCycleTimeDeltaDays)}d ${data.kpis.avgCycleTimeDeltaDays <= 0 ? 'under' : 'over'} the 15-day target`}
              onClick={() => goToQueue({ status: ['Closed'], sort: 'cycleDays', dir: 'desc' })}
            />
            <KpiCard
              label="Clarification rounds per submission"
              value={data.kpis.clarificationRoundsPerSubmission}
              secondary={`Team average: ${data.kpis.clarificationRoundsTeamAvg}`}
              onClick={() => goToQueue({ sort: 'clarificationRounds', dir: 'desc' })}
            />
          </>
        ) : (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] rounded-md" />)
        )}
      </div>

      {!data ? (
        <div className="grid grid-cols-2 gap-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-[340px] rounded-md" />
          ))}
        </div>
      ) : (
        <motion.div className="grid grid-cols-2 gap-5" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.08 } } }}>
          {/* Row 1 */}
          <motion.div variants={fadeUp}>
            <ChartCard title="Category mix of submissions" insight={insight('categoryMix')} legend={<CategoryLegend categories={data.categoryMix.map((d) => d.category)} />}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.categoryMix.map((d) => ({ ...d, label: SHORT_CATEGORY[d.category] }))} layout="vertical" margin={{ left: 24, right: 44 }}>
                  <CartesianGrid stroke="#e5e8e6" horizontal={false} />
                  <XAxis type="number" domain={[0, categoryMixDomainMax]} tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} allowDecimals={false} />
                  <YAxis type="category" dataKey="label" width={110} tick={{ fontSize: 10.5, fill: '#285055' }} tickLine={false} axisLine={false} interval={0} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} formatter={(value, _n, item) => [`${value} · ${item?.payload?.sharePct}%`, 'Submissions']} />
                  <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                    {data.categoryMix.map((d) => (
                      <Cell key={d.category} fill={CATEGORY_COLORS[d.category]} />
                    ))}
                    <LabelList
                      dataKey="count"
                      position="right"
                      content={(props) => {
                        const { x, y, width, height, index } = props as { x: number; y: number; width: number; height: number; index: number };
                        const row = data.categoryMix[index];
                        return (
                          <text x={Number(x) + Number(width) + 6} y={Number(y) + Number(height) / 2} dy={4} fontSize={10.5} fill="#285055">
                            {row.count} · {row.sharePct}%
                          </text>
                        );
                      }}
                    />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </motion.div>

          <motion.div variants={fadeUp}>
            <ChartCard title="Clarification rounds: you vs. team average" insight={insight('clarificationRoundsComparison')}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.clarificationRoundsComparison.map((d) => ({ ...d, label: SHORT_CATEGORY[d.category] }))}>
                  <CartesianGrid stroke="#e5e8e6" vertical={false} />
                  <XAxis dataKey="label" interval={0} tick={<CategoryXTick />} tickLine={false} axisLine={{ stroke: '#dde3e2' }} />
                  <YAxis tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} domain={[0, 2]} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} />
                  <ReferenceLine y={data.teamOverallAverageRounds} stroke="#B98A1E" strokeDasharray="4 3" label={{ value: `Team avg ${data.teamOverallAverageRounds}`, position: 'insideTopRight', fontSize: 10, fill: '#8a6a1e' }} />
                  <Bar dataKey="you" name="You" fill="#194247" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="team" name="Team average" fill="#94DEA5" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </motion.div>

          {/* Row 2 - full width */}
          <motion.div variants={fadeUp} className="col-span-2">
            <ChartCard title="Clarification types by category" insight={insight('clarificationTypesByCategory')} legend={<TypeLegend types={data.clarificationTypes} />}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.clarificationTypesByCategory.map((d) => ({ ...d, ...d.counts, label: SHORT_CATEGORY[d.category] }))} layout="vertical" margin={{ left: 24, right: 36 }}>
                  <CartesianGrid stroke="#e5e8e6" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} allowDecimals={false} />
                  <YAxis type="category" dataKey="label" width={110} tick={<CategoryYTick />} tickLine={false} axisLine={false} interval={0} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} />
                  {data.clarificationTypes.map((t, i) => {
                    const isLast = i === data.clarificationTypes.length - 1;
                    return (
                      <Bar key={t} dataKey={t} stackId="a" fill={TYPE_COLORS[t]} radius={isLast ? [0, 4, 4, 0] : 0}>
                        {isLast && <LabelList dataKey="total" position="right" fontSize={11} fill="#285055" />}
                      </Bar>
                    );
                  })}
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </motion.div>

          {/* Row 3 */}
          <motion.div variants={fadeUp}>
            <ChartCard title="Cycle time trend" insight={insight('cycleTimeTrend')} chip="Fixed: last 6 months">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data.cycleTimeTrend}>
                  <CartesianGrid stroke="#e5e8e6" vertical={false} />
                  <XAxis dataKey="month" interval={0} tick={{ fontSize: 10, fill: '#52787d' }} tickLine={false} axisLine={{ stroke: '#dde3e2' }} />
                  <YAxis domain={[0, 16]} tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} />
                  <ReferenceLine y={data.slaTargetDays} stroke="#8a3324" strokeDasharray="4 3" label={{ value: `${data.slaTargetDays}-day target`, position: 'insideBottomRight', fontSize: 10, fill: '#8a3324' }} />
                  <Line type="monotone" dataKey="avgDays" stroke="#194247" strokeWidth={2.5} dot={{ r: 3.5, fill: '#194247' }} isAnimationActive />
                </LineChart>
              </ResponsiveContainer>
            </ChartCard>
          </motion.div>

          <motion.div variants={fadeUp}>
            <ChartCard title="Outcome distribution" insight={insight('outcomeDistribution')}>
              <div className="h-full relative">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={data.outcomeDistribution} dataKey="count" nameKey="outcome" innerRadius={55} outerRadius={82} paddingAngle={2}>
                      {data.outcomeDistribution.map((d, i) => (
                        <Cell key={i} fill={d.outcome === 'Approved' ? '#52E081' : '#8a3324'} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                  <span className="text-2xl font-bold text-text-primary leading-none">{data.outcomeTotal}</span>
                  <span className="text-[10.5px] text-text-faint mt-0.5">decisions</span>
                </div>
                <div className="absolute bottom-0 left-0 right-0 flex justify-center gap-4">
                  <span className="flex items-center gap-1.5 text-[11px] font-medium text-text-secondary">
                    <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: '#52E081' }} />
                    Approved
                  </span>
                  <span className="flex items-center gap-1.5 text-[11px] font-medium text-text-secondary">
                    <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: '#8a3324' }} />
                    Rejected
                  </span>
                </div>
              </div>
            </ChartCard>
          </motion.div>
        </motion.div>
      )}

      <p className="text-[11.5px] text-text-faint text-center px-4">
        Charts on this page are computed from a generated 78-case corpus representing six months of submission volume across the FCRM programme (April-September 2026). The metric cards above reflect your actual cases.
      </p>
    </div>
  );
}
