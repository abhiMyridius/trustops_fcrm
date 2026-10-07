/**
 * The signed-in chrome: sidebar navigation chosen by the principal's role claim, a topbar with the
 * session menu, and the routed page. Navigation differs by role; visibility is decided by the server.
 */
import { useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
  Activity,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  LayoutDashboard,
  ListChecks,
  Plug,
  Plus,
  Settings2,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react';
import { useCreateCase } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import { useAuth } from '@/auth/AuthProvider';
import { DEMO_CASE_ID, ROLE_LABEL } from '@/auth/roles';
import type { Role } from '@/api/types/common';
import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { BrandMark } from '@/components/ui/BrandMark';
import { Button } from '@/components/ui/Button';
import { Dropdown } from '@/components/ui/Dropdown';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/cn';

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

const NAV: Record<Role, NavItem[]> = {
  fcrm_po: [
    { to: '/po', label: 'Dashboard', icon: LayoutDashboard, end: true },
    { to: '/queue', label: 'My queue', icon: ClipboardList },
    { to: '/integrations', label: 'Integrations', icon: Plug },
  ],
  fcrm_analyst: [
    { to: '/analyst', label: 'Dashboard', icon: LayoutDashboard, end: true },
    { to: '/analyst/queue', label: 'Queue', icon: ClipboardList },
    { to: '/analyst/assurance', label: 'Assurance', icon: ShieldCheck },
    { to: '/analyst/config', label: 'Config', icon: Settings2 },
  ],
  fcrm_committee: [{ to: '/committee', label: 'Queue', icon: ListChecks, end: true }],
  fcrm_observer: [{ to: `/monitor/${DEMO_CASE_ID}`, label: 'Harness monitor', icon: Activity }],
};

export function AppShell() {
  const { user, role, logout } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const create = useCreateCase();
  const [collapsed, setCollapsed] = useState(false);

  if (!user || !role) return null;
  const items = NAV[role];

  async function newCase() {
    try {
      const c = await create.mutateAsync({});
      navigate(`/case/${c.id}/documents`);
      toast.success(`Case ${c.id} created`, 'The case id is issued first; upload what you already have.');
    } catch (err) {
      toast.error('Could not create the case', userMessage(err));
    }
  }

  async function signOut(to: string) {
    await logout();
    navigate(to, { replace: true });
  }

  return (
    <div className="flex min-h-screen bg-surface-tertiary">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <aside
        aria-label="Primary"
        className={cn(
          'no-print sticky top-0 flex h-screen shrink-0 flex-col bg-carbon-moss text-text-on-dark transition-[width] duration-200',
          collapsed ? 'w-[72px]' : 'w-[260px]',
        )}
      >
        <div className={cn('flex flex-col gap-3 border-b border-pulse-green-alpha-12 py-5', collapsed ? 'px-3' : 'px-5')}>
          <div className={cn('flex items-center', collapsed ? 'justify-center' : 'justify-end')}>
            <button
              type="button"
              onClick={() => setCollapsed((v) => !v)}
              aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              aria-expanded={!collapsed}
              className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-full text-text-on-dark-muted hover:bg-pulse-green-alpha-6 hover:text-text-on-dark"
            >
              {collapsed ? <ChevronRight aria-hidden size={16} /> : <ChevronLeft aria-hidden size={16} />}
            </button>
          </div>
          {collapsed ? (
            <span className="text-center text-[19px] font-extrabold text-text-on-dark">T</span>
          ) : (
            <div className="flex flex-col leading-tight">
              <span className="text-[21px] font-extrabold tracking-tight text-text-on-dark">TrustOps</span>
              <span className="mt-1 text-[10px] font-semibold uppercase tracking-overline text-pulse-green">Risk Assessment Workbench</span>
            </div>
          )}
        </div>
        {role === 'fcrm_po' && (
          <div className={cn('mt-4', collapsed ? 'px-2.5' : 'px-3')}>
            <Button variant="primary" className="w-full" onClick={() => void newCase()} loading={create.isPending} icon={<Plus aria-hidden size={16} />} aria-label="New case">
              {!collapsed && 'New case'}
            </Button>
          </div>
        )}
        <nav aria-label="Main" className="mt-4 flex flex-col gap-0.5">
          {items.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              end={it.end}
              title={collapsed ? it.label : undefined}
              className={({ isActive }) =>
                cn(
                  'relative flex items-center gap-3 py-2.5 text-[14px] font-medium transition-colors hover:no-underline',
                  collapsed ? 'justify-center px-0' : 'px-5',
                  isActive
                    ? 'bg-pulse-green-alpha-6 font-semibold text-text-on-dark before:absolute before:inset-y-1.5 before:left-0 before:w-1 before:rounded-r-full before:bg-pulse-green'
                    : 'text-text-on-dark-muted hover:bg-pulse-green-alpha-6 hover:text-text-on-dark',
                )
              }
            >
              <it.icon aria-hidden size={20} className="shrink-0" />
              <span className={cn(collapsed && 'sr-only')}>{it.label}</span>
            </NavLink>
          ))}
        </nav>
        {!collapsed && (
          <div className="mt-auto flex flex-col items-start gap-3 border-t border-pulse-green-alpha-12 px-5 py-4">
            <BrandMark tone="dark" className="h-8" />
            <p className="text-[11px] text-text-on-dark-muted">
              Synthetic data only · seed P7
              <br />
              Nothing is approved or rejected automatically.
            </p>
          </div>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          data-chrome
          className="no-print sticky top-0 z-30 flex h-14 items-center justify-between gap-4 border-b border-border bg-linen/85 px-6 backdrop-blur-md backdrop-saturate-[1.4]"
        >
          <div className="flex items-center gap-2">
            <Badge tone="neutral">{ROLE_LABEL[role]}</Badge>
          </div>
          <Dropdown
            label={`Account menu for ${user.name}`}
            trigger={
              <>
                <Avatar name={user.name} size="sm" decorative />
                <span className="hidden text-left sm:block">
                  <span className="block text-[13px] font-semibold leading-tight">{user.name}</span>
                  <span className="block text-[11px] leading-tight text-text-muted">{user.title}</span>
                </span>
                <ChevronDown aria-hidden size={14} />
              </>
            }
            items={[
              ...(role !== 'fcrm_po'
                ? [{ key: 'monitor', label: 'Open harness monitor (FCRM-2026-0151)', onSelect: () => window.open(`/monitor/${DEMO_CASE_ID}`, '_blank', 'noopener') }]
                : []),
              { key: 'switch', label: 'Switch user (signs you out)', onSelect: () => void signOut('/login') },
              { key: 'logout', label: 'Sign out', danger: true, onSelect: () => void signOut('/login') },
            ]}
          />
        </header>
        <main id="main" tabIndex={-1} className="flex-1 overflow-x-hidden px-6 py-6 outline-none lg:px-8">
          <div className="mx-auto w-full max-w-[1280px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
