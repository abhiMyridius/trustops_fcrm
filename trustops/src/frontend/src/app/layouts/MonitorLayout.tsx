import { Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/Button';

/** Standalone window for the harness monitor (opened beside the PO or analyst screen in the demo). */
export function MonitorLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="flex min-h-full flex-col bg-linen">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <header
        data-chrome
        className="flex h-14 items-center justify-between border-b border-border bg-linen/85 px-5 backdrop-blur-md backdrop-saturate-[1.4]"
      >
        <p className="text-sm font-extrabold tracking-tight text-text-primary">
          TrustOps <span className="font-normal text-text-muted">· harness monitor</span>
        </p>
        <div className="flex items-center gap-3 text-[12px] text-text-muted">
          <span>{user?.name}</span>
          <Button
            size="sm"
            variant="ghost"
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
