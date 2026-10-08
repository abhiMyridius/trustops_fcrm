import type { ReactNode } from 'react';
import { useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';

export interface RadioGroupProps<T extends string> {
  legend: ReactNode;
  name: string;
  value: T | null;
  onChange: (value: T) => void;
  options: { value: T; label: ReactNode; description?: ReactNode; disabled?: boolean }[];
  hideLegend?: boolean;
  orientation?: 'vertical' | 'horizontal';
}

export function RadioGroup<T extends string>({
  legend,
  name,
  value,
  onChange,
  options,
  hideLegend,
  orientation = 'vertical',
}: RadioGroupProps<T>) {
  const base = useStableId('rg');
  return (
    <fieldset className="flex flex-col gap-2 border-0 p-0 m-0">
      <legend className={cn('text-[13px] font-semibold text-text-secondary mb-1', hideLegend && 'sr-only')}>{legend}</legend>
      <div className={cn('flex gap-2', orientation === 'vertical' ? 'flex-col' : 'flex-row flex-wrap')}>
        {options.map((o) => {
          const id = `${base}-${o.value}`;
          return (
            <div key={o.value} className="flex items-start gap-2.5">
              <input
                id={id}
                type="radio"
                name={name}
                value={o.value}
                checked={value === o.value}
                disabled={o.disabled}
                onChange={() => onChange(o.value)}
                className="mt-0.5 h-4 w-4 cursor-pointer accent-virtual-tide"
              />
              <label htmlFor={id} className="text-sm cursor-pointer">
                {o.label}
                {o.description && <span className="block text-xs text-text-muted">{o.description}</span>}
              </label>
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}
