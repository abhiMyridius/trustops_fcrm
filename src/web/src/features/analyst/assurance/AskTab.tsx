/** 2.22 Assurance › Ask: the examiner answers from the audit chain only; every sentence cites a record. */
import { useState } from 'react';
import { MessageSquareQuote } from 'lucide-react';
import { useExaminerAsk, useVersions } from '@/api/queries/assuranceQueries';
import { userMessage } from '@/api/errors';
import { AuditRecordChip } from '@/components/domain/AuditRecordChip';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Tag } from '@/components/ui/Badge';
import { useDocumentTitle } from '@/lib/a11y';

const CASE = 'FCRM-2026-0151';

export function AskTab() {
  useDocumentTitle('Ask', 'Assurance');
  const [question, setQuestion] = useState('Why was FCRM-2026-0151 routed to committee instead of closed by the analyst?');
  const ask = useExaminerAsk();
  const versions = useVersions(CASE);
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_320px]">
      <div className="flex flex-col gap-5">
        <Card title="Ask the examiner" subtitle={`Case ${CASE} · examiner permitted tool: audit_log (read)`}>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              ask.mutate({ question, case_id: CASE });
            }}
          >
            <Input label="Question" value={question} onChange={(e) => setQuestion(e.target.value)} />
            <div>
              <Button type="submit" variant="primary" icon={<MessageSquareQuote aria-hidden size={15} />} disabled={!question.trim()} loading={ask.isPending}>
                Ask
              </Button>
            </div>
          </form>
          <Banner tone="rule" className="mt-4">
            Every sentence cites a record. Nothing outside the chain.
          </Banner>
        </Card>
        {ask.isError && <Banner tone="danger">{userMessage(ask.error)}</Banner>}
        {ask.data && (
          <Card title="Answer" subtitle={ask.data.question}>
            {ask.data.refused ? (
              <p className="text-sm font-semibold text-text-secondary">The audit chain does not contain the answer to that question.</p>
            ) : (
              <ol className="flex flex-col gap-2.5 text-sm" aria-live="polite">
                {ask.data.sentences.map((s, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-1.5 leading-relaxed">
                    <span>{s.t}</span>
                    {s.refs.map((r) => (
                      <AuditRecordChip key={r} seq={r} href={`/analyst/case/${CASE}/activity#rec-${r}`} />
                    ))}
                  </li>
                ))}
              </ol>
            )}
            {ask.data.references.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1.5">
                {ask.data.references.map((r) => (
                  <Tag key={r}>{r}</Tag>
                ))}
              </div>
            )}
          </Card>
        )}
      </div>
      <Card title="Versions in force for the case">
        {versions.data && versions.data.length > 0 ? (
          <table className="w-full text-sm">
            <caption className="sr-only">Pinned versions for {CASE}</caption>
            <tbody>
              {versions.data.map((v) => (
                <tr key={v.item} className="border-t border-border-hairline">
                  <th scope="row" className="py-1.5 text-left font-medium text-text-secondary">
                    {v.item.replace('_', ' ')}
                  </th>
                  <td className="py-1.5 text-right font-mono text-[12px]">{v.version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-text-muted">Loading…</p>
        )}
      </Card>
    </div>
  );
}
