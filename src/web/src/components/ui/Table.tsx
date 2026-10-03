/**
 * Data table: sticky head, optional sortable headers (aria-sort), skeleton rows while loading and a
 * specific empty message. Rows may be links (whole row clickable via the first cell's link) or plain.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import { cn } from '@/lib/cn';
import { Skeleton } from './Skeleton';

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** Present when the column can be sorted; returns the comparable value. */
  sortValue?: (row: T) => string | number;
  align?: 'left' | 'right' | 'center';
  className?: string;
  headerClassName?: string;
}

export interface TableProps<T> {
  columns: Column<T>[];
  rows: T[] | undefined;
  rowKey: (row: T) => string;
  caption: string;
  loading?: boolean;
  empty?: ReactNode;
  rowClassName?: (row: T) => string | undefined;
  captionVisible?: boolean;
  dense?: boolean;
}

type Sort = { key: string; dir: 'asc' | 'desc' } | null;

export function Table<T>({ columns, rows, rowKey, caption, loading, empty, rowClassName, captionVisible, dense }: TableProps<T>) {
  const [sort, setSort] = useState<Sort>(null);

  const sorted = useMemo(() => {
    if (!rows || !sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const val = col.sortValue;
    return [...rows].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      const cmp = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb));
      return sort.dir === 'asc' ? cmp : -cmp;
    });
  }, [rows, sort, columns]);

  const toggle = (key: string) =>
    setSort((s) => (s?.key === key ? (s.dir === 'asc' ? { key, dir: 'desc' } : null) : { key, dir: 'asc' }));

  const pad = dense ? 'px-3 py-2' : 'px-4 py-3';

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full border-collapse text-sm">
        <caption className={cn(captionVisible ? 'px-4 py-2 text-left text-xs text-text-muted' : 'sr-only')}>{caption}</caption>
        <thead className="sticky top-0 z-[1] bg-surface-tertiary">
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              const ariaSort = active ? (sort?.dir === 'asc' ? 'ascending' : 'descending') : c.sortValue ? 'none' : undefined;
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={ariaSort}
                  className={cn(
                    'border-b border-border text-left text-[12px] font-semibold uppercase tracking-wide text-text-muted',
                    pad,
                    c.align === 'right' && 'text-right',
                    c.align === 'center' && 'text-center',
                    c.headerClassName,
                  )}
                >
                  {c.sortValue ? (
                    <button type="button" onClick={() => toggle(c.key)} className="inline-flex cursor-pointer items-center gap-1 uppercase hover:text-text-primary">
                      {c.header}
                      {active ? (
                        sort?.dir === 'asc' ? <ArrowUp aria-hidden size={12} /> : <ArrowDown aria-hidden size={12} />
                      ) : (
                        <ArrowUpDown aria-hidden size={12} className="opacity-50" />
                      )}
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading &&
            Array.from({ length: 5 }, (_, i) => (
              <tr key={`sk-${i}`}>
                {columns.map((c) => (
                  <td key={c.key} className={cn('border-b border-border-hairline', pad)}>
                    <Skeleton className="h-4 w-full max-w-40" />
                  </td>
                ))}
              </tr>
            ))}
          {!loading && sorted && sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="px-4 py-10 text-center text-sm text-text-muted">
                {empty ?? 'Nothing to show.'}
              </td>
            </tr>
          )}
          {!loading &&
            sorted?.map((row) => (
              <tr key={rowKey(row)} className={cn('hover:bg-ink-2', rowClassName?.(row))}>
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      'border-b border-border-hairline align-top text-text-primary',
                      pad,
                      c.align === 'right' && 'text-right tabular-nums',
                      c.align === 'center' && 'text-center',
                      c.className,
                    )}
                  >
                    {c.cell(row)}
                  </td>
                ))}
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
