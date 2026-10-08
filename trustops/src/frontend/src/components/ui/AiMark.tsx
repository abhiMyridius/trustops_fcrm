import { useId, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface AiMarkProps {
  /** Pixel size of the square mark. */
  size?: number;
  /** Rotate the two sparkles, as in the original eva.svg. On by default; pass false for a still mark.
   *  Always still for people who ask their system for reduced motion. */
  animated?: boolean;
  className?: string;
}

const LARGE =
  'M13.5203 0.614014C13.6341 0.405936 13.9328 0.405936 14.0466 0.614014L18.5671 8.87964C18.5947 8.92998 18.6369 8.97125 18.6873 8.99878L26.9529 13.5193C27.161 13.6331 27.161 13.9319 26.9529 14.0457L18.6873 18.5662C18.6369 18.5937 18.5947 18.635 18.5671 18.6853L14.0466 26.9509C13.9328 27.1588 13.6341 27.1588 13.5203 26.9509L8.99976 18.6853C8.97222 18.635 8.92998 18.5937 8.87964 18.5662L0.614014 14.0457C0.405922 13.9319 0.40592 13.6331 0.614014 13.5193L8.87964 8.99878C8.92998 8.97125 8.97222 8.92998 8.99976 8.87964L13.5203 0.614014Z';
const SMALL =
  'M26.7791 20.699C26.8929 20.4909 27.1916 20.4909 27.3054 20.699L29.4128 24.5525C29.4404 24.6028 29.4827 24.6441 29.533 24.6716L33.3865 26.7791C33.5944 26.8929 33.5945 27.1916 33.3865 27.3054L29.533 29.4128C29.4826 29.4404 29.4404 29.4816 29.4128 29.532L27.3054 33.3855C27.1916 33.5936 26.8929 33.5936 26.7791 33.3855L24.6716 29.532C24.6441 29.4816 24.6019 29.4404 24.5515 29.4128L20.698 27.3054C20.49 27.1916 20.4901 26.8929 20.698 26.7791L24.5515 24.6716C24.6018 24.6441 24.6441 24.6028 24.6716 24.5525L26.7791 20.699Z';

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** The mark followed by its text, for a title or line an AI agent produced. */
export function AiLabel({ children, size = 16, animated, className }: AiMarkProps & { children: ReactNode }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <AiMark size={size} animated={animated} />
      {children}
    </span>
  );
}

/**
 * The AI marker (the "eva" sparkles): put it next to anything an AI agent proposed, drafted or is
 * doing. Decorative: the words beside it already say what the AI did, so it is hidden from
 * assistive technology and never changes an accessible name.
 */
export function AiMark({ size = 16, animated = true, className }: AiMarkProps) {
  const gradient = useId();
  const spin = animated && !prefersReducedMotion();
  return (
    <svg aria-hidden="true" focusable="false" width={size} height={size} viewBox="0 0 34 34" fill="none" className={cn('inline-block shrink-0 align-[-0.15em]', className)}>
      <defs>
        <linearGradient id={gradient} x1="9.51389" y1="9.51357" x2="30.3094" y2="31.1417" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--color-digital-pavo)" />
          <stop offset="1" stopColor="var(--color-pulse-green)" />
        </linearGradient>
      </defs>
      <g>
        {spin && <animateTransform attributeName="transform" type="rotate" values="0 27.042 27.042; 45 27.042 27.042; 0 27.042 27.042" dur="2s" repeatCount="indefinite" />}
        <path d={SMALL} fill={`url(#${gradient})`} />
      </g>
      <g>
        {spin && <animateTransform attributeName="transform" type="rotate" values="0 13.78 13.78; 45 13.78 13.78; 0 13.78 13.78" dur="2s" repeatCount="indefinite" />}
        <path d={LARGE} fill={`url(#${gradient})`} />
      </g>
    </svg>
  );
}
