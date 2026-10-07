import { cn } from '@/lib/cn';

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? '') + (parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? '') : '')).toUpperCase();
}

/** Initials avatar. Decorative when the name is printed next to it; otherwise it carries the name. */
export function Avatar({ name, size = 'md', decorative = false }: { name: string; size?: 'sm' | 'md' | 'lg'; decorative?: boolean }) {
  const dims = { sm: 'h-6 w-6 text-[10px]', md: 'h-8 w-8 text-xs', lg: 'h-10 w-10 text-sm' }[size];
  return (
    <span
      role={decorative ? undefined : 'img'}
      aria-label={decorative ? undefined : name}
      aria-hidden={decorative || undefined}
      className={cn('inline-flex shrink-0 items-center justify-center rounded-full bg-pulse-green-tint-2 font-bold text-quantum-leaf', dims)}
    >
      {initials(name)}
    </span>
  );
}
