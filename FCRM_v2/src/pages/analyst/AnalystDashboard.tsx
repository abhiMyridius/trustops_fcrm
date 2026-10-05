import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { getAnalystDashboard } from '@/services/analystApi';
import type { AnalystDashboardData } from '@/types/analyst';
import type { AmlCategory } from '@/types/analyst';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Input } from '@/components/ui/Form';
import { useCountUp } from '@/hooks/useCountUp';
import { Icon } from '@/components/ui/Icon';

type TimePreset = 'this-month' | 'last-3-months' | 'custom';

const SHORT_AML: Record<AmlCategory, string> = {
  'Customer / entity': 'Customer',
  'Product and service': 'Product',
  Geography: 'Geography',
  'Delivery channel and third party': 'Delivery',
};

const STAGE_COLORS: Record<string, string> = { Clarify: '#194247', 'Policy Mapping': '#94DEA5', 'Risk & Controls': '#8a3324' };
const BAND_COLORS: Record<number, string> = { 1: '#52E081', 2: '#94DEA5', 3: '#B98A1E', 4: '#8a3324' };

function resolveWindow(preset: TimePreset, customFrom: string, customTo: string): { from: string; to: string; label: string } {
  const now = new Date();
  if (preset === 'custom') {
    if (!customFrom || !customTo) return { from: '2100-01-01T00:00:00Z', to: '2100-01-02T00:00:00Z', label: 'the selected range' };
    const fromD = new Date(`${customFrom}T00:00:00Z`);
    const toD = new Date(`${customTo}T23:59:59Z`);
    const fmt = (d: Date) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    return { from: fromD.toISOString(), to: toD.toISOString(), label: `${fmt(fromD)} - ${fmt(toD)}` };
  }
  const monthsBack = preset === 'this-month' ? 0 : 2;
  const fromD = new Date(now.getFullYear(), now.getMonth() - monthsBack, 1);
  const label =
    preset === 'this-month'
      ? `this month (${now.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })})`
      : `the last 3 months (${fromD.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} - ${now.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })})`;
  return { from: fromD.toISOString(), to: now.toISOString(), label };
}

function KpiCard({ label, value, suffix }: { label: string; value: number; suffix?: string }) {
  const count = useCountUp(value);
  return (
    <Card material="flat" className="p-5 flex flex-col gap-1.5">
      <span className="text-[13px] font-semibold text-text-muted">{label}</span>
      <span className="font-sans text-[32px] font-bold text-text-primary leading-none">
        {count}
        {suffix}
      </span>
    </Card>
  );
}

function ChartEmptyState({ label }: { label: string }) {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-2 text-center px-4">
      <Icon name="search" size={20} className="text-text-faint" />
      <span className="text-[12.5px] text-text-muted">No cases fall in {label}.</span>
      <span className="text-[11.5px] text-text-faint">Try a wider range.</span>
    </div>
  );
}

function ChartCard({ title, insight, chip, legend, children, cta }: { title: string; insight: string; chip?: string; legend?: React.ReactNode; children: React.ReactNode; cta?: React.ReactNode }) {
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
      {cta}
    </Card>
  );
}

function StatStrip({ title, items }: { title: string; items: { label: string; value: string | number; dot?: string }[] }) {
  return (
    <Card material="flat" className="p-4 flex items-center gap-6 flex-wrap">
      <span className="text-[12.5px] font-bold text-text-primary shrink-0">{title}</span>
      <div className="flex items-center gap-5 flex-wrap">
        {items.map((it) => (
          <span key={it.label} className="flex items-center gap-1.5 text-[12.5px] text-text-secondary">
            {it.dot && <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: it.dot }} />}
            <span className="font-semibold text-text-primary">{it.value}</span>
            {it.label}
          </span>
        ))}
      </div>
    </Card>
  );
}

const fadeUp = { hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0 } };

export function AnalystDashboard() {
  const [data, setData] = useState<AnalystDashboardData | null>(null);
  const [preset, setPreset] = useState<TimePreset>('last-3-months');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const navigate = useNavigate();

  const window_ = useMemo(() => resolveWindow(preset, customFrom, customTo), [preset, customFrom, customTo]);

  useEffect(() => {
    setData(null);
    getAnalystDashboard('Priya Raman', window_).then(setData);
  }, [window_.from, window_.to, window_.label]);

  // Lets a link from Config's AI Agents page (§10.8) land directly on the override-rate chart.
  // The charts below measure their own size on mount (ResponsiveContainer), which briefly changes
  // page height right after data arrives - the delay lets that settle before scrolling.
  useEffect(() => {
    if (!data || !window.location.hash) return;
    const id = setTimeout(() => document.querySelector(window.location.hash)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
    return () => clearTimeout(id);
  }, [data]);

  const insight = (id: string) => data?.insights.find((i) => i.chartId === id)?.insightText ?? '';

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-[26px] font-bold text-text-primary">Dashboard</h1>
          <p className="text-[13.5px] text-text-muted mt-1">What needs you, where your time goes, whether you're calibrated, and whether the AI is worth trusting.</p>
        </div>
        <div className="flex items-center gap-2 bg-white border border-border-strong rounded-md p-1">
          {(['this-month', 'last-3-months', 'custom'] as TimePreset[]).map((p) => (
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
      </div>

      <div className="grid grid-cols-4 gap-4">
        {data ? (
          <>
            <KpiCard label="Open cases assigned to me" value={data.kpis.openCasesAssignedToMe} />
            <KpiCard label="Approaching SLA breach" value={data.kpis.casesApproachingSlaBreach} />
            <KpiCard label="Avg analyst-active cycle time" value={data.kpis.avgAnalystActiveCycleTimeDays} suffix=" days" />
            <KpiCard label="Clarification rounds per case" value={data.kpis.clarificationRoundsPerCase} />
          </>
        ) : (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[92px] rounded-md" />)
        )}
      </div>

      <div>
        <h2 className="text-[15px] font-bold text-text-primary mb-3">Caseload and calibration</h2>
        {!data ? (
          <div className="grid grid-cols-2 gap-5">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-[340px] rounded-md" />
            ))}
          </div>
        ) : (
          <motion.div className="grid grid-cols-2 gap-5" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.08 } } }}>
            <motion.div variants={fadeUp}>
              <ChartCard title="Avg cycle time by stage" insight={insight('cycle-time-stage')}>
                {data.isWindowEmpty ? (
                  <ChartEmptyState label={data.windowLabel} />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={data.cycleTimeByStage}>
                      <CartesianGrid stroke="#e5e8e6" vertical={false} />
                      <XAxis dataKey="stage" interval={0} tick={{ fontSize: 9.5, fill: '#52787d' }} tickLine={false} axisLine={{ stroke: '#dde3e2' }} />
                      <YAxis tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} />
                      <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} formatter={(value) => [`${value} days`, 'Avg']} />
                      <Bar dataKey="avgDays" radius={[4, 4, 0, 0]}>
                        {data.cycleTimeByStage.map((s) => (
                          <Cell key={s.stage} fill={s.secondary ? '#c7cfce' : '#194247'} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>
            </motion.div>

            <motion.div variants={fadeUp}>
              <ChartCard title="Cycle time trend vs target" insight={insight('cycle-time-trend')} chip="Fixed: last 6 months">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={data.cycleTimeTrend}>
                    <CartesianGrid stroke="#e5e8e6" vertical={false} />
                    <XAxis dataKey="month" interval={0} tick={{ fontSize: 10, fill: '#52787d' }} tickLine={false} axisLine={{ stroke: '#dde3e2' }} />
                    <YAxis domain={[0, 18]} tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} />
                    <ReferenceLine y={data.slaTargetDays} stroke="#8a3324" strokeDasharray="4 3" label={{ value: `${data.slaTargetDays}-day target`, position: 'insideBottomRight', fontSize: 10, fill: '#8a3324' }} />
                    <Line type="monotone" dataKey="avgDays" stroke="#194247" strokeWidth={2.5} dot={{ r: 3.5, fill: '#194247' }} isAnimationActive />
                  </LineChart>
                </ResponsiveContainer>
              </ChartCard>
            </motion.div>

            <motion.div variants={fadeUp}>
              <ChartCard title="My residual vs team median, by AML category" insight={insight('residual-vs-median')}>
                {data.isWindowEmpty ? (
                  <ChartEmptyState label={data.windowLabel} />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={data.residualVsMedian.map((d) => ({ ...d, label: SHORT_AML[d.category] }))}>
                      <CartesianGrid stroke="#e5e8e6" vertical={false} />
                      <XAxis dataKey="label" interval={0} tick={{ fontSize: 9.5, fill: '#52787d' }} tickLine={false} axisLine={{ stroke: '#dde3e2' }} />
                      <YAxis tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} />
                      <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} labelFormatter={(_v, item) => item?.[0]?.payload?.category ?? ''} />
                      <Bar dataKey="you" name="You" fill="#194247" radius={[4, 4, 0, 0]} />
                      <Bar dataKey="teamMedian" name="Team median" fill="#94DEA5" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>
            </motion.div>

            <motion.div variants={fadeUp}>
              <ChartCard title="Inherent vs residual by category" insight={insight('inherent-vs-residual')}>
                {data.isWindowEmpty ? (
                  <ChartEmptyState label={data.windowLabel} />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={data.inherentVsResidual.map((d) => ({ ...d, label: SHORT_AML[d.category] }))}>
                      <CartesianGrid stroke="#e5e8e6" vertical={false} />
                      <XAxis dataKey="label" interval={0} tick={{ fontSize: 9.5, fill: '#52787d' }} tickLine={false} axisLine={{ stroke: '#dde3e2' }} />
                      <YAxis tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} />
                      <Tooltip
                        contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }}
                        labelFormatter={(_v, item) => item?.[0]?.payload?.category ?? ''}
                        formatter={(value, name, item) => [value, name === 'inherent' ? 'Inherent' : `Residual (${item?.payload?.reductionPct}% reduction)`]}
                      />
                      <Bar dataKey="inherent" name="inherent" fill="#c7cfce" radius={[4, 4, 0, 0]} />
                      <Bar dataKey="residual" name="residual" fill="#09A78D" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>
            </motion.div>
          </motion.div>
        )}

        {!data ? (
          <Skeleton className="h-[260px] rounded-md mt-5" />
        ) : (
          <motion.div initial="hidden" animate="show" variants={fadeUp} className="mt-5">
            <ChartCard
              title="Clarifications by field, stacked by stage raised"
              insight={insight('clarifications-by-field')}
              legend={
                <div className="flex flex-wrap gap-x-3.5 gap-y-1.5">
                  {data.clarificationStages.map((s) => (
                    <span key={s} className="flex items-center gap-1.5 text-[11px] font-medium text-text-secondary">
                      <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: STAGE_COLORS[s] }} />
                      {s}
                    </span>
                  ))}
                </div>
              }
              cta={
                <button onClick={() => navigate('/analyst/config')} className="text-[12.5px] font-semibold text-carbon-moss hover:underline cursor-pointer self-start flex items-center gap-1">
                  Review in Categories & Fields
                  <Icon name="arrow-right" size={13} />
                </button>
              }
            >
              {data.isWindowEmpty ? (
                <ChartEmptyState label={data.windowLabel} />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.clarificationsByField.map((d) => ({ ...d, ...d.counts }))} layout="vertical" margin={{ left: 24, right: 36 }}>
                    <CartesianGrid stroke="#e5e8e6" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} allowDecimals={false} />
                    <YAxis type="category" dataKey="field" width={230} tick={{ fontSize: 10.5, fill: '#285055' }} tickLine={false} axisLine={false} interval={0} />
                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} />
                    {data.clarificationStages.map((stage, i) => (
                      <Bar key={stage} dataKey={stage} stackId="a" fill={STAGE_COLORS[stage]} radius={i === data.clarificationStages.length - 1 ? [0, 4, 4, 0] : 0} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              )}
            </ChartCard>
          </motion.div>
        )}

        {data && (
          <div className="mt-5">
            <StatStrip
              title="Band distribution"
              items={
                data.isWindowEmpty
                  ? [{ label: `No cases in ${data.windowLabel}`, value: '-' }]
                  : data.bandDistribution.map((b) => ({ label: `Band ${b.band}`, value: `${b.count} (${b.sharePct}%)`, dot: BAND_COLORS[b.band] }))
              }
            />
          </div>
        )}
      </div>

      <div id="ai-quality">
        <h2 className="text-[15px] font-bold text-text-primary mb-3">AI quality</h2>
        {!data ? (
          <div className="grid grid-cols-2 gap-5">
            <Skeleton className="h-[340px] rounded-md" />
            <Skeleton className="h-[340px] rounded-md" />
          </div>
        ) : (
          <motion.div className="grid grid-cols-2 gap-5" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.08 } } }}>
            <motion.div variants={fadeUp}>
              <ChartCard title="Override rate per agent" insight={insight('override-rate-agent')}>
                {data.isWindowEmpty ? (
                  <ChartEmptyState label={data.windowLabel} />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={data.overrideRatePerAgent}>
                      <CartesianGrid stroke="#e5e8e6" vertical={false} />
                      <XAxis dataKey="agent" interval={0} tick={{ fontSize: 8.5, fill: '#52787d' }} tickLine={false} axisLine={{ stroke: '#dde3e2' }} />
                      <YAxis tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} unit="%" />
                      <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} formatter={(value, _name, item) => [`${value}%`, `${item?.payload?.agent} override rate`]} />
                      <Bar dataKey="overrideRatePct" fill="#194247" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>
            </motion.div>

            <motion.div variants={fadeUp}>
              <ChartCard title="Policy mapping trend" insight={insight('policy-mapping-trend')} chip="Fixed: last 6 months">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={data.policyMappingTrend}>
                    <CartesianGrid stroke="#e5e8e6" vertical={false} />
                    <XAxis dataKey="month" interval={0} tick={{ fontSize: 10, fill: '#52787d' }} tickLine={false} axisLine={{ stroke: '#dde3e2' }} />
                    <YAxis tick={{ fontSize: 11, fill: '#52787d' }} tickLine={false} axisLine={false} allowDecimals={false} />
                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #dde3e2' }} />
                    <Line type="monotone" dataKey="accepted" name="Accepted" stroke="#52E081" strokeWidth={2.5} dot={{ r: 3, fill: '#52E081' }} />
                    <Line type="monotone" dataKey="removed" name="Removed" stroke="#8a3324" strokeWidth={2} dot={{ r: 3, fill: '#8a3324' }} />
                    <Line type="monotone" dataKey="analystAdded" name="Analyst-added" stroke="#337077" strokeWidth={2} dot={{ r: 3, fill: '#337077' }} />
                  </LineChart>
                </ResponsiveContainer>
                <div className="flex flex-wrap gap-x-3.5 gap-y-1.5 -mt-1">
                  {[
                    { label: 'Accepted', color: '#52E081' },
                    { label: 'Removed', color: '#8a3324' },
                    { label: 'Analyst-added', color: '#337077' },
                  ].map((l) => (
                    <span key={l.label} className="flex items-center gap-1.5 text-[11px] font-medium text-text-secondary">
                      <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: l.color }} />
                      {l.label}
                    </span>
                  ))}
                </div>
              </ChartCard>
            </motion.div>
          </motion.div>
        )}

        {data && (
          <div className="mt-5">
            <StatStrip
              title="Control inventory health"
              items={[
                { label: 'inventory size', value: data.controlInventoryHealth.inventorySize },
                { label: 'analyst-added controls', value: data.controlInventoryHealth.analystAddedControls },
                { label: 'judgment-only controls', value: data.controlInventoryHealth.judgmentOnlyControls },
                { label: 'controls with stale evidence', value: data.controlInventoryHealth.staleEvidenceControls },
              ]}
            />
          </div>
        )}
      </div>

      <p className="text-[11.5px] text-text-faint text-center px-4">
        The charts on this page are computed from a generated 78-case corpus (April-September 2026), the same one behind the PO dashboard. Your case counts, SLA figures and queue reflect your actual cases.
      </p>
    </div>
  );
}
