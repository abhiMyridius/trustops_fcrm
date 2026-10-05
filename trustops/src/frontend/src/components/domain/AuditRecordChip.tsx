import { Link2 } from 'lucide-react';

/** "#49 · human.override" chip. With `href` it links to the record in the case activity view. */
export function AuditRecordChip({ seq, type, href }: { seq: number; type?: string; href?: string }) {
  const body = (
    <>
      <Link2 aria-hidden size={11} />#{seq}
      {type ? ` · ${type}` : ''}
    </>
  );
  const cls = 'inline-flex items-center gap-1 rounded-sm bg-info-bg px-1.5 py-0.5 font-mono text-[11px] font-semibold text-chart-deep-1';
  if (href) {
    return (
      <a href={href} className={`${cls} hover:no-underline hover:ring-1 hover:ring-info`} aria-label={`Audit record ${seq}${type ? `, ${type}` : ''}`}>
        {body}
      </a>
    );
  }
  return (
    <span className={cls} aria-label={`Audit record ${seq}${type ? `, ${type}` : ''}`}>
      {body}
    </span>
  );
}
