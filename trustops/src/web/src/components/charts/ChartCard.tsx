/**
 * Chart frame: title, a one-line insight (what the chart says), the recharts child, and a visually
 * hidden data table so the figures are available without the picture (design C §1.8).
 */
import type { ReactNode } from 'react';
import { Card } from '@/components/ui/Card';

export interface ChartDatum {
  label: string;
  value: number | string;
}

export function ChartCard({
  title,
  insight,
  children,
  data,
  height = 220,
}: {
  title: string;
  insight: ReactNode;
  children: ReactNode;
  data: ChartDatum[];
  height?: number;
}) {
  return (
    <Card title={title} subtitle={insight}>
      <div aria-hidden style={{ height }} className="w-full">
        {children}
      </div>
      <table className="sr-only">
        <caption>{title}</caption>
        <tbody>
          {data.map((d) => (
            <tr key={d.label}>
              <th scope="row">{d.label}</th>
              <td>{d.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
