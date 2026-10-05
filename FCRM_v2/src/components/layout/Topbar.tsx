import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useLocation, useNavigate } from 'react-router-dom';
import { Icon } from '@/components/ui/Icon';
import { getNotifications, markAllNotificationsRead, markNotificationRead } from '@/services/api';
import type { Notification } from '@/types';
import { timeAgo } from '@/utils/format';
import { ANALYST_USER, COMMITTEE_USER, PO_USER, type Persona, usePersona } from '@/context/PersonaContext';

export function Topbar() {
  const [open, setOpen] = useState(false);
  const [personaMenuOpen, setPersonaMenuOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[] | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const personaRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const { persona, setPersona, user } = usePersona();

  useEffect(() => {
    getNotifications().then(setNotifications);
  }, []);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
      if (personaRef.current && !personaRef.current.contains(e.target as Node)) setPersonaMenuOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  const unread = notifications?.filter((n) => !n.read).length ?? 0;

  async function handleItemClick(n: Notification) {
    await markNotificationRead(n.id);
    setNotifications((cur) => cur?.map((x) => (x.id === n.id ? { ...x, read: true } : x)) ?? null);
    if (n.caseId) navigate(caseUrl(persona, n.caseId));
    setOpen(false);
  }

  async function handleMarkAll() {
    await markAllNotificationsRead();
    setNotifications((cur) => cur?.map((x) => ({ ...x, read: true })) ?? null);
  }

  function caseUrl(p: Persona, caseId: string) {
    if (p === 'po') return `/case/${caseId}`;
    if (p === 'analyst') return `/analyst/case/${caseId}`;
    return `/committee/case/${caseId}`;
  }

  function switchPersona(next: Persona) {
    setPersonaMenuOpen(false);
    if (next === persona) return;
    const caseMatch = location.pathname.match(/^\/(?:analyst\/case|committee\/case|case)\/([^/]+)/);
    setPersona(next);
    if (caseMatch) {
      navigate(caseUrl(next, caseMatch[1]));
    } else {
      navigate(next === 'po' ? '/' : next === 'analyst' ? '/analyst' : '/committee');
    }
  }

  return (
    <header className="h-16 shrink-0 border-b border-border bg-white flex items-center justify-end px-6 sticky top-0 z-30">
      <div className="flex items-center gap-4">
        <div className="relative" ref={ref}>
          <button onClick={() => setOpen((v) => !v)} className="relative w-9 h-9 rounded-full flex items-center justify-center hover:bg-surface-tertiary cursor-pointer text-text-secondary">
            <Icon name="bell" size={19} />
            {unread > 0 && (
              <span className="absolute top-1 right-1 w-4 h-4 rounded-full bg-danger text-white text-[9px] font-bold flex items-center justify-center">{unread}</span>
            )}
          </button>
          <AnimatePresence>
            {open && (
              <motion.div
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.15 }}
                className="absolute right-0 mt-2 w-[360px] bg-white border border-border-strong rounded-lg shadow-lg overflow-hidden"
              >
                <div className="flex items-center justify-between px-4 py-3 border-b border-border-hairline">
                  <span className="text-sm font-bold text-text-primary">Notifications</span>
                  <button onClick={handleMarkAll} className="text-[12px] font-semibold text-carbon-moss hover:underline cursor-pointer">
                    Mark all read
                  </button>
                </div>
                <div className="max-h-[360px] overflow-y-auto">
                  {notifications?.length === 0 && <div className="px-4 py-8 text-center text-[13px] text-text-muted">No notifications yet.</div>}
                  {notifications?.map((n) => (
                    <button
                      key={n.id}
                      onClick={() => handleItemClick(n)}
                      className="w-full text-left px-4 py-3 border-b border-border-hairline last:border-b-0 hover:bg-surface-tertiary cursor-pointer flex gap-2.5"
                    >
                      {!n.read && <span className="w-1.5 h-1.5 rounded-full bg-teal mt-1.5 shrink-0" />}
                      <div className={n.read ? 'pl-3.5' : ''}>
                        <div className="text-[13px] font-semibold text-text-primary">{n.title}</div>
                        <div className="text-[12.5px] text-text-secondary mt-0.5">{n.body}</div>
                        <div className="text-[11px] text-text-faint mt-1">{timeAgo(n.timestamp)}</div>
                      </div>
                    </button>
                  ))}
                </div>
                <button
                  onClick={() => {
                    navigate('/settings/notifications');
                    setOpen(false);
                  }}
                  className="w-full text-center py-2.5 text-[12.5px] font-semibold text-carbon-moss hover:bg-surface-tertiary cursor-pointer border-t border-border-hairline"
                >
                  Notification settings
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <div className="relative pl-4 border-l border-border-hairline" ref={personaRef}>
          <button
            onClick={() => setPersonaMenuOpen((v) => !v)}
            className="flex items-center gap-2.5 cursor-pointer hover:bg-surface-tertiary rounded-md px-2 py-1 -mx-2 -my-1"
          >
            <div className="w-8 h-8 rounded-full bg-carbon-moss text-white text-[12px] font-bold flex items-center justify-center shrink-0">{user.initials}</div>
            <div className="flex flex-col leading-tight items-start">
              <span className="text-[13px] font-semibold text-text-primary">{user.name}</span>
              <span className="text-[11px] text-text-muted">{user.role}</span>
            </div>
            <Icon name="chevron-down" size={14} className="text-text-faint" />
          </button>
          <AnimatePresence>
            {personaMenuOpen && (
              <motion.div
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.15 }}
                className="absolute right-0 mt-2 w-[260px] bg-white border border-border-strong rounded-lg shadow-lg overflow-hidden z-40"
              >
                <div className="px-4 py-2.5 border-b border-border-hairline">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Switch persona</span>
                </div>
                {[PO_USER, ANALYST_USER, COMMITTEE_USER].map((u) => {
                  const p: Persona = u === PO_USER ? 'po' : u === ANALYST_USER ? 'analyst' : 'committee';
                  return (
                    <button
                      key={p}
                      onClick={() => switchPersona(p)}
                      className={`w-full text-left flex items-center gap-3 px-4 py-3 hover:bg-surface-tertiary cursor-pointer ${persona === p ? 'bg-verdant-1' : ''}`}
                    >
                      <div className="w-8 h-8 rounded-full bg-carbon-moss-deep text-quantum-leaf text-[12px] font-bold flex items-center justify-center shrink-0">{u.initials}</div>
                      <div className="flex flex-col leading-tight">
                        <span className="text-[13px] font-semibold text-text-primary">{u.name}</span>
                        <span className="text-[11px] text-text-muted">{u.role}</span>
                      </div>
                      {persona === p && <Icon name="check" size={14} className="ml-auto text-teal" />}
                    </button>
                  );
                })}
                <div className="px-4 py-2 border-t border-border-hairline text-[10.5px] text-text-faint">Demo affordance - switches which permission set renders the same case data.</div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </header>
  );
}
