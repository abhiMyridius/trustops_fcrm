import { cn } from '@/lib/cn';

export interface BrandMarkProps {
  /** Which surface the mark sits on: `dark` uses the variant with a linen wordmark, `light` the one
   *  with a dark teal wordmark. Both are transparent, so no chip or background is needed. */
  tone?: 'light' | 'dark';
  className?: string;
}

/** The Myridius brand mark (`public/brand-mark*.png`, from the supplied logo), placed bottom-left per the guide. */
export function BrandMark({ tone = 'light', className }: BrandMarkProps) {
  return (
    <img
      src={tone === 'dark' ? '/brand-mark-dark.png' : '/brand-mark.png'}
      alt="Myridius"
      className={cn('w-auto', className)}
    />
  );
}
