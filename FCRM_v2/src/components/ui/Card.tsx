import type { HTMLAttributes } from 'react';
import clsx from 'clsx';

type Material = 'flat' | 'raised' | 'elevated' | 'dark';

const materialClasses: Record<Material, string> = {
  flat: 'bg-white border border-border-strong rounded-md',
  raised: 'bg-white border border-border-strong rounded-lg shadow-md',
  elevated: 'bg-white rounded-xl shadow-xl',
  dark: 'bg-carbon-moss-deep text-white rounded-md',
};

export function Card({ material = 'flat', className, ...rest }: HTMLAttributes<HTMLDivElement> & { material?: Material }) {
  return <div className={clsx(materialClasses[material], className)} {...rest} />;
}
