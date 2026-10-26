import type { ConfigSectionBase, RoutingRulesData } from '@/api/types/config';
import { Card } from '@/components/ui/Card';
import { DraftEdit } from '../DraftEdit';

/** 6. Rules & Routing: routing v4 R1–R6 as data rows; thresholds editable into the draft. */
export function RoutingRules({ s }: { s: ConfigSectionBase<'routing-rules', RoutingRulesData> }) {
  return (
    <div className="flex flex-col gap-5">
      <Card title={`Routing rules ${s.active_version}`}>
        <ol className="flex flex-col gap-2 text-sm">
          {s.data.rules.map((r) => (
            <li key={r.id} className="flex gap-3">
              <span className="font-mono text-[12px] font-bold text-quantum-leaf">{r.id}</span>
              <span>{r.rule}</span>
            </li>
          ))}
        </ol>
      </Card>
      <Card
        title="Parameters"
        subtitle="Edit band edges in the Scoring Model section. The SLA target sets every case's due date: business days from submission to a decision-ready case."
      >
        <table className="w-full text-sm">
          <caption className="sr-only">Routing parameters</caption>
          <tbody>
            {s.data.parameters.map((p) => (
              <tr key={p.key} className="border-t border-border-hairline">
                <th scope="row" className="py-2 text-left font-medium">
                  {p.label}
                </th>
                <td className="py-2 text-right font-bold">{p.value}</td>
                <td className="w-20 py-2 text-right">
                  {p.editable && <DraftEdit section={s.section} etag={s.draft.etag} path={p.key} label={p.label} current={p.value} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
