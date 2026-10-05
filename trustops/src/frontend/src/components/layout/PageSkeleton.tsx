import { Skeleton } from '@/components/ui/Skeleton';

export function PageSkeleton({ label = 'Loading' }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-4 p-8">
      <Skeleton className="h-7 w-72" />
      <Skeleton className="h-4 w-96" />
      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}
