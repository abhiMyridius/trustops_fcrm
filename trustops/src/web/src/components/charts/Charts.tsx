/** recharts configs with the shared palette: one bar chart, one line chart. No 3-D, no dual axes. */
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { CHART, axisTick } from './palette';

export function SimpleBarChart({
  data,
  colorFor,
  layout = 'horizontal',
  valueSuffix = '',
}: {
  data: { label: string; value: number; key?: string | number }[];
  colorFor?: (d: { label: string; value: number; key?: string | number }, i: number) => string;
  layout?: 'horizontal' | 'vertical';
  valueSuffix?: string;
}) {
  const vertical = layout === 'vertical';
  return (
    <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 400, height: 200 }}>
      <BarChart data={data} layout={vertical ? 'vertical' : 'horizontal'} margin={{ top: 16, right: 28, bottom: 4, left: vertical ? 8 : -12 }}>
        <CartesianGrid stroke={CHART.grid} horizontal={!vertical} vertical={vertical} />
        {vertical ? (
          <>
            <XAxis type="number" tick={axisTick} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="label" tick={axisTick} axisLine={false} tickLine={false} width={96} />
          </>
        ) : (
          <>
            <XAxis dataKey="label" tick={axisTick} axisLine={false} tickLine={false} />
            <YAxis tick={axisTick} axisLine={false} tickLine={false} />
          </>
        )}
        <Bar dataKey="value" radius={vertical ? [0, 4, 4, 0] : [4, 4, 0, 0]} isAnimationActive={false}>
          {data.map((d, i) => (
            <Cell key={d.label} fill={colorFor ? colorFor(d, i) : CHART.series[0]} />
          ))}
          <LabelList dataKey="value" position={vertical ? 'right' : 'top'} fill={CHART.label} fontSize={11} formatter={(v: unknown) => `${String(v)}${valueSuffix}`} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function SimpleLineChart({ data, valueSuffix = '' }: { data: { label: string; value: number }[]; valueSuffix?: string }) {
  return (
    <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 400, height: 200 }}>
      <LineChart data={data} margin={{ top: 16, right: 24, bottom: 4, left: -12 }}>
        <CartesianGrid stroke={CHART.grid} vertical={false} />
        <XAxis dataKey="label" tick={axisTick} axisLine={false} tickLine={false} />
        <YAxis tick={axisTick} axisLine={false} tickLine={false} domain={['dataMin - 5', 'dataMax + 2']} />
        <Line type="monotone" dataKey="value" stroke={CHART.series[1]} strokeWidth={2.5} dot={{ r: 3, fill: CHART.series[1] }} isAnimationActive={false}>
          <LabelList dataKey="value" position="top" fill={CHART.label} fontSize={11} formatter={(v: unknown) => `${String(v)}${valueSuffix}`} />
        </Line>
      </LineChart>
    </ResponsiveContainer>
  );
}
