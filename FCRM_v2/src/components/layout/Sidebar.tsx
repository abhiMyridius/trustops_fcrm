import { useEffect, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { Icon, type IconName } from '@/components/ui/Icon';
import { createDraftCase } from '@/services/api';
import evoqLogo from '@/assets/evoq-logo.svg';
import { usePersona } from '@/context/PersonaContext';

const PO_NAV_ITEMS: { to: string; label: string; icon: IconName }[] = [
  { to: '/', label: 'Dashboard', icon: 'dashboard' },
  { to: '/queue', label: 'My Queue', icon: 'queue' },
  { to: '/templates', label: 'Templates', icon: 'templates' },
  { to: '/integrations', label: 'Integrations', icon: 'integrations' },
  { to: '/ai-configuration', label: 'AI Configuration', icon: 'ai-config' },
  { to: '/settings/notifications', label: 'Settings', icon: 'settings' },
];

const ANALYST_NAV_ITEMS: { to: string; label: string; icon: IconName }[] = [
  { to: '/analyst', label: 'Dashboard', icon: 'dashboard' },
  { to: '/analyst/queue', label: 'Queue', icon: 'queue' },
  { to: '/analyst/assurance', label: 'Assurance', icon: 'search' },
  { to: '/analyst/config', label: 'Config', icon: 'ai-config' },
];

// Committee scope is deliberately narrow (§8.5 mod, Part C): one screen to find cases, one to
// vote on them - no dashboard, no Assurance, no Config, no analyst queue.
const COMMITTEE_NAV_ITEMS: { to: string; label: string; icon: IconName }[] = [{ to: '/committee', label: 'Queue', icon: 'queue' }];

const STORAGE_KEY = 'trustops-sidebar-collapsed';

function readStoredCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

export function Sidebar() {
  const navigate = useNavigate();
  const { persona } = usePersona();
  const items = persona === 'po' ? PO_NAV_ITEMS : persona === 'analyst' ? ANALYST_NAV_ITEMS : COMMITTEE_NAV_ITEMS;
  const [collapsed, setCollapsed] = useState(readStoredCollapsed);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
    } catch {
      // per-viewer convenience only - fine if it can't persist
    }
  }, [collapsed]);

  async function handleNewCase() {
    const { caseId } = await createDraftCase();
    navigate(`/case/${caseId}/intake`);
  }

  return (
    <aside
      className={clsx(
        'shrink-0 bg-white border-r border-border flex flex-col h-screen sticky top-0 transition-[width] duration-200 ease-out overflow-hidden',
        collapsed ? 'w-[68px]' : 'w-[220px]',
      )}
    >
      <div className={clsx('py-5 flex flex-col gap-3 border-b border-border-hairline', collapsed ? 'px-3' : 'px-5')}>
        <div className={clsx('flex items-center', collapsed ? 'justify-center' : 'justify-between')}>
          {!collapsed && <img src={evoqLogo} alt="Evoq" className="h-3 w-auto opacity-70" />}
          <button
            onClick={() => setCollapsed((v) => !v)}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="w-7 h-7 rounded-md flex items-center justify-center text-text-faint hover:bg-surface-tertiary hover:text-text-secondary cursor-pointer transition-colors shrink-0"
          >
            <Icon name={collapsed ? 'chevron-right' : 'chevron-left'} size={16} />
          </button>
        </div>
        {!collapsed && (
          <div className="flex flex-col leading-tight">
            <span className="text-[21px] font-bold text-carbon-moss tracking-tight">TrustOps</span>
            <span className="text-[10px] font-semibold uppercase tracking-wide text-text-muted mt-1">Risk Assessment Workbench</span>
          </div>
        )}
        {collapsed && <span className="text-[19px] font-bold text-carbon-moss tracking-tight text-center">T</span>}
      </div>

      {persona === 'po' && (
        <div className={clsx('mb-2', collapsed ? 'px-2.5 mt-3' : 'px-3')}>
          <button
            onClick={handleNewCase}
            title="New Case"
            className={clsx(
              'w-full flex items-center justify-center gap-2 bg-carbon-moss hover:bg-carbon-moss-hover text-white text-sm font-semibold rounded-md cursor-pointer transition-colors',
              collapsed ? 'p-2.5' : 'px-4 py-2.5',
            )}
          >
            <Icon name="plus" size={16} />
            {!collapsed && 'New Case'}
          </button>
        </div>
      )}

      <nav className={clsx('flex flex-col gap-0.5', collapsed ? 'px-2.5 mt-3' : 'px-3', persona === 'po' ? 'mt-2' : 'mt-4')}>
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/' || item.to === '/analyst' || item.to === '/committee'}
            title={collapsed ? item.label : undefined}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-2.5 py-2.5 rounded-md text-[14px] font-medium transition-colors',
                collapsed ? 'px-2.5 justify-center' : 'px-3',
                isActive ? 'bg-[#e1e6e4] text-[#14363b] font-semibold' : 'text-text-secondary hover:bg-surface-tertiary',
              )
            }
          >
            <Icon name={item.icon} size={17} className="shrink-0" />
            {!collapsed && <span className="truncate">{item.label}</span>}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
