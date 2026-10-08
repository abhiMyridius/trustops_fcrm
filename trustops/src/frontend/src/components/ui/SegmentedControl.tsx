import { cn } from '@/lib/cn';

/** Single-choice filter (radio semantics). Used for queue scope and filter toggles. */
export function SegmentedControl<T extends string>({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-full border border-border-strong bg-surface p-0.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cn(
              'cursor-pointer rounded-full px-3 py-1.5 text-[13px] font-semibold transition-colors',
              active ? 'bg-quantum-leaf text-text-on-dark' : 'text-text-secondary hover:bg-surface-tertiary',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
