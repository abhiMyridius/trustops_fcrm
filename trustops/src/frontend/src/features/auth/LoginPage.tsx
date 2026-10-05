/**
 * 2.1 Login. "Sign in with corporate SSO" opens the seeded-user picker in demo mode (C-05,
 * VITE_DEMO_USERS=true); otherwise a username/password form posts to auth/login. In MSW mode a
 * scenario switch picks the demo's starting scene (mock-only control, never shown against live).
 */
import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowRight, KeyRound, ShieldCheck } from 'lucide-react';
import { api } from '@/api/client';
import { userMessage } from '@/api/errors';
import { useDemoUsers } from '@/api/queries/authQueries';
import type { DemoUser } from '@/api/types/common';
import { useAuth } from '@/auth/AuthProvider';
import { homeFor } from '@/auth/roles';
import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';

const demoMode = (import.meta.env.VITE_DEMO_USERS ?? 'true') === 'true';
const mswMode = (import.meta.env.VITE_API_MODE ?? 'msw') === 'msw';

interface Scenario {
  key: string;
  label: string;
}

function ScenarioSwitch() {
  const toast = useToast();
  const scenarios = useQuery({ queryKey: ['mock', 'scenarios'], queryFn: () => api<Scenario[]>('/api/fcrm/__mock/scenarios'), staleTime: Infinity });
  const [key, setKey] = useState(() => {
    try {
      return window.localStorage.getItem('trustops.mock.scenario') ?? 'fresh';
    } catch {
      return 'fresh';
    }
  });
  const apply = useMutation({
    mutationFn: (k: string) => api('/api/fcrm/__mock/scenario', { method: 'POST', body: { key: k } }),
    onSuccess: () => toast.success('Demo state reset', scenarios.data?.find((s) => s.key === key)?.label),
    onError: (e) => toast.error('Could not reset', userMessage(e)),
  });
  if (!scenarios.data) return null;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed border-border-strong bg-surface-tertiary p-4">
      <p className="text-[12px] font-semibold uppercase tracking-wide text-text-muted">Mock backend only · demo starting point</p>
      <div className="flex flex-wrap items-end gap-2">
        <Select
          label="Scenario for FCRM-2026-0151"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          options={scenarios.data.map((s) => ({ value: s.key, label: s.label }))}
          wrapperClassName="min-w-0 flex-1"
        />
        <Button variant="secondary" onClick={() => apply.mutate(key)} loading={apply.isPending}>
          Reset to scenario
        </Button>
      </div>
      <p className="text-[12px] text-text-muted">Switching user keeps the state; this button rebuilds it from the seed.</p>
    </div>
  );
}

export function LoginPage() {
  useDocumentTitle('Sign in');
  const { login } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const toast = useToast();
  const [ssoOpen, setSsoOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const users = useDemoUsers(demoMode && ssoOpen);

  async function signIn(u: string, p?: string) {
    setBusy(u);
    try {
      const principal = await login(u, p);
      const next = params.get('next');
      const role = principal.roles[0];
      navigate(next && next.startsWith('/') ? next : role ? homeFor(role) : '/', { replace: true });
    } catch (err) {
      toast.error('Sign-in failed', userMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <main id="main" className="grid w-full grid-cols-1 lg:grid-cols-[1.1fr_1fr]">
      <section className="hidden flex-col justify-between p-12 text-text-on-dark lg:flex">
        <img src="/brand-mark.svg" alt="Myridius" className="h-5 w-auto self-start rounded bg-surface px-2 py-1" />
        <div className="max-w-md">
          <p className="text-[13px] font-semibold uppercase tracking-wide text-quantum-leaf">TrustOps · FCRM risk assessment workbench</p>
          <p className="mt-3 text-4xl font-bold leading-tight">One governed place for change risk assessments</p>
          <ul className="mt-6 flex flex-col gap-2 text-sm text-text-on-dark-muted">
            <li>Scores come only from the deterministic engine; agents propose, people decide.</li>
            <li>Policy is quoted word for word with its source.</li>
            <li>Every decision is written to a hash-chained audit trail.</li>
          </ul>
        </div>
        <p className="text-[12px] text-text-on-dark-muted">Synthetic data only. Genius Hacks Q3 2026.</p>
      </section>

      <section className="flex items-center justify-center bg-surface p-6 sm:p-10">
        <div className="flex w-full max-w-md flex-col gap-6">
          <div>
            <h1 className="text-2xl font-bold text-text-primary">Sign in to TrustOps</h1>
            <p className="mt-1 text-sm font-semibold text-text-secondary lg:hidden">One governed place for change risk assessments</p>
            <p className="mt-2 text-sm text-text-secondary">Your role and what you can see are set by the bank&apos;s directory, not by this app.</p>
          </div>

          <Button variant="primary" size="lg" icon={<ShieldCheck aria-hidden size={18} />} onClick={() => setSsoOpen(true)} aria-expanded={ssoOpen}>
            Sign in with corporate SSO
          </Button>

          {ssoOpen && demoMode && (
            <div className="flex flex-col gap-4" aria-live="polite">
              <p className="text-[13px] text-text-muted">Demo directory: choose a seeded person. No password is collected in demo mode.</p>
              {users.isPending && <p className="text-sm text-text-muted">Loading the directory…</p>}
              {users.data?.map((g) => (
                <fieldset key={g.role} className="m-0 flex flex-col gap-1.5 border-0 p-0">
                  <legend className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-text-muted">{g.label}</legend>
                  {g.users.map((u: DemoUser) => (
                    <button
                      key={u.username}
                      type="button"
                      onClick={() => void signIn(u.username)}
                      disabled={busy !== null}
                      className="flex cursor-pointer items-center gap-3 rounded-lg border border-border px-3 py-2.5 text-left hover:border-carbon-moss hover:bg-surface-tertiary disabled:cursor-wait"
                    >
                      <Avatar name={u.name} size="sm" decorative />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold text-text-primary">{u.name}</span>
                        <span className="block text-[12px] text-text-muted">
                          {u.title}
                          {u.unit ? ` · ${u.unit}` : ''}
                        </span>
                      </span>
                      <ArrowRight aria-hidden size={16} className="text-text-faint" />
                      <span className="sr-only">Sign in as {u.name}</span>
                    </button>
                  ))}
                </fieldset>
              ))}
            </div>
          )}

          {ssoOpen && !demoMode && (
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                void signIn(username, password);
              }}
            >
              <Input label="Username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
              <Input label="Password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
              <Button type="submit" variant="primary" icon={<KeyRound aria-hidden size={16} />} loading={busy !== null}>
                Sign in
              </Button>
            </form>
          )}

          {mswMode && <ScenarioSwitch />}
        </div>
      </section>
    </main>
  );
}
