/** Accessibility helpers: focusable query, focus trap, stable ids, document titles. */
import { useEffect, useId, useRef, type RefObject } from 'react';

export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.hasAttribute('aria-hidden'));
}

/**
 * Traps Tab inside `ref` while `active`, closes on Escape, restores focus to the opener on close.
 * Used by Modal and Drawer (WAI-ARIA dialog pattern).
 */
export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean, onEscape?: () => void): void {
  // The latest handler is read through a ref so a parent re-render never re-runs the trap (and steals focus).
  const escapeRef = useRef(onEscape);
  useEffect(() => {
    escapeRef.current = onEscape;
  });
  useEffect(() => {
    if (!active) return;
    const root = ref.current;
    if (!root) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = focusableIn(root)[0];
    (root.querySelector<HTMLElement>('[data-autofocus]') ?? first ?? root).focus();

    function onKey(e: KeyboardEvent) {
      const esc = escapeRef.current;
      if (e.key === 'Escape' && esc) {
        e.stopPropagation();
        esc();
        return;
      }
      if (e.key !== 'Tab' || !root) return;
      const items = focusableIn(root);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const head = items[0];
      const tail = items[items.length - 1];
      if (e.shiftKey && document.activeElement === head) {
        e.preventDefault();
        tail?.focus();
      } else if (!e.shiftKey && document.activeElement === tail) {
        e.preventDefault();
        head?.focus();
      }
    }
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      opener?.focus();
    };
  }, [ref, active]);
}

export function useStableId(prefix: string): string {
  return `${prefix}-${useId().replace(/:/g, '')}`;
}

/** "Risk & Controls · FCRM-2026-0151 · TrustOps" (design C §1.8). */
export function useDocumentTitle(...parts: (string | undefined | null)[]): void {
  const title = [...parts.filter(Boolean), 'TrustOps'].join(' · ');
  useEffect(() => {
    document.title = title;
  }, [title]);
}
