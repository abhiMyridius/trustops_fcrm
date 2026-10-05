/**
 * Menu button (WAI-ARIA menu pattern): Enter/Space/ArrowDown opens and focuses the first item,
 * arrows move, Home/End jump, Escape closes and returns focus to the button, Tab closes.
 */
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';

export interface DropdownItem {
  key: string;
  label: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}

export interface DropdownProps {
  label: string;
  trigger: ReactNode;
  items: DropdownItem[];
  align?: 'left' | 'right';
  triggerClassName?: string;
}

function focusMenuItem(root: HTMLElement | null, idx: number): void {
  const els = root?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])');
  if (!els || els.length === 0) return;
  els[(idx + els.length) % els.length]?.focus();
}

export function Dropdown({ label, trigger, items, align = 'right', triggerClassName }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const id = useStableId('menu');
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  const focusItem = (idx: number) => focusMenuItem(menu.current, idx);

  useEffect(() => {
    if (!open) return;
    focusMenuItem(menu.current, 0);
    const onDoc = (e: MouseEvent) => {
      if (!menu.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const els = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? []);
    const idx = els.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      focusItem(idx + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      focusItem(idx - 1);
    } else if (e.key === 'Home') {
      e.preventDefault();
      focusItem(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      focusItem(els.length - 1);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      btn.current?.focus();
    } else if (e.key === 'Tab') {
      setOpen(false);
    }
  };

  return (
    <div className="relative inline-flex">
      <button
        ref={btn}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={label}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className={cn('inline-flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-tertiary', triggerClassName)}
      >
        {trigger}
      </button>
      {open && (
        <div
          ref={menu}
          id={id}
          role="menu"
          aria-label={label}
          tabIndex={-1}
          onKeyDown={onMenuKey}
          className={cn('absolute top-full z-40 mt-1 min-w-52 rounded-lg border border-border bg-surface py-1 shadow-lg', align === 'right' ? 'right-0' : 'left-0')}
        >
          {items.map((it) => (
            <div
              key={it.key}
              role="menuitem"
              tabIndex={-1}
              aria-disabled={it.disabled || undefined}
              onClick={() => {
                if (it.disabled) return;
                setOpen(false);
                it.onSelect();
              }}
              onKeyDown={(e) => {
                if ((e.key === 'Enter' || e.key === ' ') && !it.disabled) {
                  e.preventDefault();
                  setOpen(false);
                  it.onSelect();
                }
              }}
              className={cn(
                'cursor-pointer px-3.5 py-2 text-sm outline-none hover:bg-surface-tertiary focus:bg-surface-tertiary',
                it.danger ? 'text-danger' : 'text-text-primary',
                it.disabled && 'cursor-not-allowed text-text-faint',
              )}
            >
              {it.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
