import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react';

// A working set spans sections (§1.1 of the unsaved-changes mod prompt) - edits accumulate here
// as they happen, independent of which Config section is currently visible, so the banner and
// its itemised list stay correct even after the analyst switches sections and back.
export interface PendingChange {
  id: string;
  label: string;
  sectionId: string;
  sectionLabel: string;
  revert: () => void;
}

interface SectionActions {
  /** Opens the (already-existing) impact preview and, on confirm, commits the whole working set. */
  publish: () => void;
  /** Reverts every pending change for this section back to its last-published state. */
  discard: () => void;
}

interface WorkingSetApi {
  changes: PendingChange[];
  addChange: (c: Omit<PendingChange, 'id'>) => void;
  revertChange: (id: string) => void;
  clearSection: (sectionId: string) => void;
  registerActions: (sectionId: string, actions: SectionActions) => void;
  requestPublish: (sectionId: string) => void;
  requestDiscard: (sectionId: string) => void;
}

const WorkingSetCtx = createContext<WorkingSetApi | null>(null);

export function WorkingSetProvider({ children }: { children: ReactNode }) {
  const [changes, setChanges] = useState<PendingChange[]>([]);
  const actionsRef = useRef<Map<string, SectionActions>>(new Map());

  const addChange = useCallback((c: Omit<PendingChange, 'id'>) => {
    setChanges((prev) => [...prev, { ...c, id: `pc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` }]);
  }, []);

  const revertChange = useCallback((id: string) => {
    setChanges((prev) => {
      const target = prev.find((c) => c.id === id);
      target?.revert();
      return prev.filter((c) => c.id !== id);
    });
  }, []);

  const clearSection = useCallback((sectionId: string) => {
    setChanges((prev) => prev.filter((c) => c.sectionId !== sectionId));
  }, []);

  const registerActions = useCallback((sectionId: string, actions: SectionActions) => {
    actionsRef.current.set(sectionId, actions);
  }, []);

  const requestPublish = useCallback((sectionId: string) => {
    actionsRef.current.get(sectionId)?.publish();
  }, []);

  const requestDiscard = useCallback((sectionId: string) => {
    actionsRef.current.get(sectionId)?.discard();
  }, []);

  return (
    <WorkingSetCtx.Provider value={{ changes, addChange, revertChange, clearSection, registerActions, requestPublish, requestDiscard }}>
      {children}
    </WorkingSetCtx.Provider>
  );
}

export function useWorkingSet(): WorkingSetApi {
  const ctx = useContext(WorkingSetCtx);
  if (!ctx) throw new Error('useWorkingSet must be used within WorkingSetProvider');
  return ctx;
}
