import type { CSSProperties } from 'react';
import clsx from 'clsx';

export function Skeleton({ className, style }: { className?: string; style?: CSSProperties }) {
  return <div className={clsx('skeleton', className)} style={style} />;
}

export function SkeletonTableRows({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="flex flex-col">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-6 px-5 py-3 border-t border-border-hairline first:border-t-0">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className="h-3.5" style={{ width: c === 0 ? '22%' : `${100 / cols - 5}%` }} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function SkeletonCard() {
  return (
    <div className="flex flex-col gap-2.5 border border-border rounded-md p-4">
      <Skeleton className="h-3 w-3/5" />
      <Skeleton className="h-3 w-4/5" />
      <Skeleton className="h-3 w-2/5" />
    </div>
  );
}
