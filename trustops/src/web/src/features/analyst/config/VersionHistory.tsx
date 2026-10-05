import type { ConfigVersion } from '@/api/types/config';
import { Badge } from '@/components/ui/Badge';
import { Card } from '@/components/ui/Card';
import { formatDate } from '@/lib/format';

/** Append-only version list: every publish is a new immutable snapshot. */
export function VersionHistory({ versions }: { versions: ConfigVersion[] }) {
  return (
    <Card title="Version history" subtitle="Append-only; cases pin the version in force when they start.">
      <ol className="flex flex-col gap-2 text-sm">
        {versions.map((v) => (
          <li key={v.version} className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[12px] font-semibold">{v.version}</span>
            {v.active && <Badge tone="success">active</Badge>}
            <span className="text-text-secondary">
              {formatDate(v.published_at)} · {v.by}
            </span>
            <span className="w-full text-[12px] text-text-muted">{v.note}</span>
          </li>
        ))}
      </ol>
    </Card>
  );
}
