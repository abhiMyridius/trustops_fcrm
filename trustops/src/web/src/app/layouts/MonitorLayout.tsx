import { Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/Button';

/** Standalone window for the harness monitor (opened beside the PO or analyst screen in the demo). */
export function MonitorLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="flex min-h-full flex-col bg-ink-2">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <header data-chrome className="flex h-12 items-center justify-between bg-carbon-moss px-5 text-text-on-dark">
        <p className="text-sm font-bold tracking-tight">
          TrustOps <span className="font-normal text-text-on-dark-muted">· harness monitor</span>
        </p>
        <div className="flex items-center gap-3 text-[12px] text-text-on-dark-muted">
          <span>{user?.name}</span>
          <Button
            size="sm"
            variant="ghost"
            className="text-text-on-dark hover:bg-carbon-moss-hover"
            onClick={() => {
              void logout().then(() => navigate('/login', { replace: true }));
            }}
          >
            Sign out
          </Button>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="flex-1 p-5 outline-none">
        <Outlet />
      </main>
    </div>
  );
}
