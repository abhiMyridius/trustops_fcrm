import { cn } from '@/lib/cn';

export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden className={cn('skeleton block h-4', className)} />;
}

/** A card-shaped placeholder so a loading section never renders blank (design C §1.9). */
export function SkeletonCard({ lines = 3, label = 'Loading' }: { lines?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="rounded-lg border border-border bg-surface p-5 shadow-sm">
      <Skeleton className="mb-4 h-5 w-48" />
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn('mb-2.5', i % 2 ? 'w-3/4' : 'w-full')} />
      ))}
    </div>
  );
}
