import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { ErrorState } from './EmptyState';
import { SkeletonCard } from './Skeleton';

/**
 * Renders a query's loading skeleton or inline error, else the children with the data.
 * Keeps every screen on the same loading/error contract (design C §1.9).
 */
export function QueryState<T>({
  query,
  children,
  skeletonLines = 4,
  label = 'Loading',
}: {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  skeletonLines?: number;
  label?: string;
}) {
  if (query.isPending) return <SkeletonCard lines={skeletonLines} label={label} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  return <>{children(query.data)}</>;
}
