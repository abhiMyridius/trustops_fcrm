/**
 * WAI-ARIA tabs. `Tabs` switches in-page panels; `RouteTabs` renders the case tabs as router links
 * (the URL is the state, design C §1.5 rule 3). Both use a roving tabindex: arrows, Home and End
 * move focus between tabs; Enter/Space (or click) activates.
 */
import { useRef, type KeyboardEvent, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useStableId } from '@/lib/a11y';
import { cn } from '@/lib/cn';
import { Pill } from './Badge';

export interface TabDef {
  key: string;
  label: string;
  count?: number;
  countLabel?: string;
}

function moveFocus(e: KeyboardEvent<HTMLElement>, container: HTMLElement | null) {
  if (!container) return;
  const tabs = Array.from(container.querySelectorAll<HTMLElement>('[role="tab"]'));
  const idx = tabs.indexOf(e.currentTarget);
  let next = -1;
  if (e.key === 'ArrowRight') next = (idx + 1) % tabs.length;
  else if (e.key === 'ArrowLeft') next = (idx - 1 + tabs.length) % tabs.length;
  else if (e.key === 'Home') next = 0;
  else if (e.key === 'End') next = tabs.length - 1;
  if (next >= 0) {
    e.preventDefault();
    tabs[next]?.focus();
  }
}

const tabClass = (active: boolean) =>
  cn(
    'relative inline-flex items-center whitespace-nowrap px-3.5 py-2.5 text-sm font-semibold transition-colors cursor-pointer',
    active
      ? 'text-carbon-moss after:absolute after:inset-x-2 after:-bottom-px after:h-[2.5px] after:rounded-full after:bg-quantum-leaf'
      : 'text-text-muted hover:text-text-primary',
  );

export interface TabsProps {
  tabs: TabDef[];
  active: string;
  onChange: (key: string) => void;
  label: string;
  children: ReactNode;
}

export function Tabs({ tabs, active, onChange, label, children }: TabsProps) {
  const id = useStableId('tabs');
  const listRef = useRef<HTMLDivElement>(null);
  return (
    <div>
      <div ref={listRef} role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            id={`${id}-tab-${t.key}`}
            type="button"
            role="tab"
            aria-selected={t.key === active}
            aria-controls={`${id}-panel`}
            tabIndex={t.key === active ? 0 : -1}
            onClick={() => onChange(t.key)}
            onKeyDown={(e) => moveFocus(e, listRef.current)}
            className={tabClass(t.key === active)}
          >
            {t.label}
            {t.count ? <Pill count={t.count} label={t.countLabel ?? 'items'} /> : null}
          </button>
        ))}
      </div>
      <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-tab-${active}`} tabIndex={0} className="pt-5 outline-none">
        {children}
      </div>
    </div>
  );
}

export interface RouteTabsProps {
  tabs: TabDef[];
  /** Base path; each tab links to `${base}/${key}`. */
  base: string;
  label: string;
  children: ReactNode;
}

export function RouteTabs({ tabs, base, label, children }: RouteTabsProps) {
  const id = useStableId('rtabs');
  const listRef = useRef<HTMLDivElement>(null);
  const { pathname } = useLocation();
  const active = tabs.find((t) => pathname === `${base}/${t.key}` || pathname.startsWith(`${base}/${t.key}/`))?.key ?? tabs[0]?.key;
  return (
    <div>
      <div ref={listRef} role="tablist" aria-label={label} className="no-print flex gap-1 overflow-x-auto border-b border-border">
        {tabs.map((t) => (
          <Link
            key={t.key}
            id={`${id}-tab-${t.key}`}
            to={`${base}/${t.key}`}
            role="tab"
            aria-selected={t.key === active}
            aria-controls={`${id}-panel`}
            tabIndex={t.key === active ? 0 : -1}
            onKeyDown={(e) => moveFocus(e, listRef.current)}
            className={cn(tabClass(t.key === active), 'hover:no-underline')}
          >
            {t.label}
            {t.count ? <Pill count={t.count} label={t.countLabel ?? 'items'} /> : null}
          </Link>
        ))}
      </div>
      <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-tab-${active ?? ''}`} className="pt-5">
        {children}
      </div>
    </div>
  );
}
