import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { personaFromPath, usePersona } from '@/context/PersonaContext';

const TITLES: Record<string, string> = {
  '/': 'Dashboard',
  '/queue': 'My Queue',
  '/templates': 'Templates',
  '/integrations': 'Integrations',
  '/ai-configuration': 'AI Configuration',
  '/settings/notifications': 'Settings',
  '/analyst': 'Dashboard',
  '/analyst/queue': 'Queue',
  '/analyst/assurance': 'Assurance',
  '/analyst/config': 'Config',
  '/committee': 'Committee Queue',
};

export function AppLayout() {
  const location = useLocation();
  const { persona, setPersona } = usePersona();

  useEffect(() => {
    const expected = personaFromPath(location.pathname);
    if (expected !== persona) setPersona(expected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  useEffect(() => {
    // The page itself renders its own heading - the topbar doesn't repeat it, so the
    // browser tab title is the only place this map's text still shows up.
    const title = TITLES[location.pathname];
    document.title = title ? `${title} - TrustOps` : 'TrustOps';
  }, [location.pathname]);

  return (
    <div className="flex min-h-screen bg-canvas">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <main className="flex-1 min-w-0">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.22, ease: 'easeOut' }}
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
