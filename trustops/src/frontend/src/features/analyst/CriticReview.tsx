/**
 * The critic's findings on the Summary, one card per finding (high first), each readable without opening
 * anything: what was found in plain words, the control, the numbers the server compared (never computed
 * here), the record's own reason, what to do, and the three ways to act: explain (the review item's
 * decision), change the rating (the control's row on Risk & controls) or ask the owner (the Inbox).
 * An explained finding collapses to one line.
 */
import { useId } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { CircleCheck, TriangleAlert } from 'lucide-react';
import type { CriticFinding, CriticFindingGate } from '@/api/types/analyst';
import { AuditRecordChip } from '@/components/domain/AuditRecordChip';
import { Badge, type Tone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { formatDate, formatFixed } from '@/lib/format';

type Finding = CriticFinding & CriticFindingGate;

const SEVERITY: Record<CriticFinding['severity'], { label: string; tone: Tone; order: number }> = {
  high: { label: 'High', tone: 'danger', order: 0 },
  medium: { label: 'Medium', tone: 'warning', order: 1 },
  low: { label: 'Low', tone: 'neutral', order: 2 },
};

/** The finding's type in plain words; a precedent deviation says which way the rating differs. */
export function findingLabel(f: CriticFinding): string {
  switch (f.type) {
    case 'precedent_deviation':
      return f.comparison && f.comparison.gap < 0 ? 'Rating higher than in similar cases' : 'Rating lower than in similar cases';
    case 'evidence_recency':
      return 'Evidence may be out of date';
    case 'rule_violation':
      return 'A rule may not be met';
    default:
      return 'Needs a look';
  }
}

function whatToDo(f: CriticFinding): string {
  switch (f.type) {
    case 'precedent_deviation':
      return f.comparison && f.comparison.gap < 0
        ? 'Confirm the high rating is supported by the evidence, or change it.'
        : 'Confirm the low rating is right (for example, the evidence has not been provided yet), or change it.';
    case 'evidence_recency':
      return 'Confirm the evidence still covers this case, ask the owner for newer evidence, or change the rating.';
    case 'rule_violation':
      return 'Check the rule against the case and explain why it is met, or change the rating.';
    default:
      return 'Read the finding and explain it, or change the rating.';
  }
}

const isExplained = (f: Finding) => f.gate_open === false;

export function CriticFindingCard({
  finding: f,
  caseId,
  closed,
  canExplain,
  onExplain,
}: {
  finding: Finding;
  caseId: string;
  /** The case is closed: no actions. */
  closed: boolean;
  /** The explain decision is available (today: an open demo case, as before). */
  canExplain: boolean;
  onExplain: (f: CriticFinding) => void;
}) {
  const navigate = useNavigate();
  const headingId = useId();
  const label = findingLabel(f);
  const sev = SEVERITY[f.severity];
  const c = f.comparison ?? null;
  const control = f.target ?? c?.control_id ?? null;
  const controlName = f.target_name ?? c?.control_name ?? null;
  const controlLine = [control, controlName].filter(Boolean).join(' · ');
  const cases = c?.cases ?? [];

  if (isExplained(f)) {
    return (
      <li className="flex flex-wrap items-center gap-2 rounded-md border border-border-hairline bg-surface-tertiary px-4 py-2 text-[13px]">
        <CircleCheck aria-hidden size={14} className="text-virtual-tide" />
        <span className="font-semibold text-text-primary">{label}</span>
        {controlLine && <span className="text-text-secondary">{controlLine}</span>}
        <span className="ml-auto text-[12px] font-semibold text-text-secondary">Explained</span>
      </li>
    );
  }

  return (
    <li aria-labelledby={headingId} className="rounded-lg border border-border border-l-4 border-l-danger bg-surface p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 id={headingId} className="flex items-center gap-2 text-[15px] font-bold tracking-tight text-text-primary">
          <TriangleAlert aria-hidden size={16} className="shrink-0 text-danger" />
          {label}
        </h3>
        <Badge tone={sev.tone}>
          <span className="sr-only">Severity: </span>
          {sev.label}
        </Badge>
      </div>
      {controlLine && <p className="mt-0.5 text-[13px] text-text-secondary">{controlLine}</p>}

      <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-[13px]">
        {c ? (
          <>
            <dt className="font-semibold text-text-muted">This case</dt>
            <dd className="tabular-nums">
              <span className="font-bold text-text-primary">{formatFixed(c.rating)}</span> / 10
            </dd>
            <dt className="font-semibold text-text-muted">Similar cases</dt>
            <dd>
              <span className="tabular-nums">
                <span className="font-bold text-text-primary">{formatFixed(c.mean)}</span> on average ({formatFixed(c.low)} – {formatFixed(c.high)})
              </span>
              {cases.length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-[12px] font-semibold text-chart-deep-1">
                    Show the {cases.length} {cases.length === 1 ? 'case' : 'cases'}
                  </summary>
                  <ul className="mt-1 flex flex-col gap-0.5 text-[12px]">
                    {cases.map((p) => (
                      <li key={p.case_id} className="flex flex-wrap gap-2 tabular-nums">
                        <Link to={`/analyst/case/${p.case_id}/summary`} className="font-mono">
                          {p.case_id}
                        </Link>
                        <span>rated {formatFixed(p.rating)}</span>
                        {p.closed_date && <span className="text-text-muted">closed {formatDate(p.closed_date)}</span>}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </dd>
          </>
        ) : (
          <>
            <dt className="font-semibold text-text-muted">Finding</dt>
            <dd className="text-text-primary">{f.text}</dd>
          </>
        )}
        {f.reason && (
          <>
            <dt className="font-semibold text-text-muted">{c ? `Why it was rated ${formatFixed(c.rating)}` : 'Why it was raised'}</dt>
            <dd className="flex flex-wrap items-center gap-2">
              <q className="italic text-text-primary">{f.reason}</q>
              {f.records.map((r) => (
                <AuditRecordChip key={r} seq={r} />
              ))}
            </dd>
          </>
        )}
        {!f.reason && f.records.length > 0 && (
          <>
            <dt className="font-semibold text-text-muted">Record</dt>
            <dd className="flex flex-wrap gap-2">
              {f.records.map((r) => (
                <AuditRecordChip key={r} seq={r} />
              ))}
            </dd>
          </>
        )}
        <dt className="font-semibold text-text-muted">What to do</dt>
        <dd>{whatToDo(f)}</dd>
      </dl>

      {!closed && (
        <div className="mt-3 flex flex-wrap gap-2">
          {canExplain && (
            <Button size="sm" variant="primary" aria-describedby={headingId} onClick={() => onExplain(f)}>
              Explain why
            </Button>
          )}
          {control && (
            <Button size="sm" variant="secondary" aria-describedby={headingId} onClick={() => navigate(`/analyst/case/${caseId}/risk-controls#control-${control}`)}>
              Change rating
            </Button>
          )}
          <Button size="sm" variant="secondary" aria-describedby={headingId} onClick={() => navigate(`/analyst/case/${caseId}/inbox`)}>
            Ask the owner for the evidence
          </Button>
        </div>
      )}
    </li>
  );
}

/** The critic's findings: a heading, one line on what they are, and one card per finding, high first. */
export function CriticReview({
  findings,
  caseId,
  closed,
  canExplain,
  onExplain,
}: {
  findings: CriticFinding[];
  caseId: string;
  closed: boolean;
  canExplain: boolean;
  onExplain: (f: CriticFinding) => void;
}) {
  const headingId = useId();
  if (findings.length === 0) return null;
  const sorted = [...(findings as Finding[])].sort((a, b) => SEVERITY[a.severity].order - SEVERITY[b.severity].order);
  const open = sorted.filter((f) => !isExplained(f)).length;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div>
        <h2 id={headingId} className="text-[15px] font-bold tracking-tight text-text-primary">
          Critic review · {open === 0 ? 'all findings explained' : `${open} ${open === 1 ? 'finding' : 'findings'} to explain`}
        </h2>
        <p className="text-[13px] text-text-secondary">
          A second AI checked the assessment. Each finding below needs your explanation before the review can finish.
        </p>
      </div>
      <ul className="flex flex-col gap-3">
        {sorted.map((f) => (
          <CriticFindingCard key={f.id} finding={f} caseId={caseId} closed={closed} canExplain={canExplain} onExplain={onExplain} />
        ))}
      </ul>
    </section>
  );
}
