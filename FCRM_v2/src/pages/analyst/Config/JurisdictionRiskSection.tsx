import { useEffect, useMemo, useState } from 'react';
import type { JurisdictionRiskEntry, JurisdictionRiskListSnapshot, JurisdictionSourceType, JurisdictionStalenessState } from '@/types/analyst';
import {
  commitJurisdictionImport,
  commitJurisdictionRiskListChange,
  getJurisdictionRiskListVersions,
  isJurisdictionInUse,
  previewJurisdictionImport,
  previewJurisdictionRiskImpact,
  validateJurisdictionEntries,
  type JurisdictionImportDiff,
  type JurisdictionRiskImpact,
} from '@/services/analystApi';
import { JURISDICTION_RISK_ANCHORS } from '@/mocks/seed/analystConfig';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Select, Textarea } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/utils/format';
import { useWorkingSet } from './WorkingSetContext';

const SECTION_ID = 'jurisdiction';
const SECTION_LABEL = 'Jurisdiction Risk List';

function newId(): string {
  return `jur-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function monthsBetween(dateStr: string): number {
  const d = new Date(dateStr);
  const now = new Date();
  return (now.getFullYear() - d.getFullYear()) * 12 + (now.getMonth() - d.getMonth());
}

// A FATF plenary sits roughly four months apart, three times a year (§10.7 mod, §6) - fixed to
// that published cadence, unlike the internal review interval, which the analyst configures below.
const EXTERNAL_NEARING_MONTHS = 4;
const EXTERNAL_STALE_MONTHS = 8;
const INTERNAL_NEARING_WINDOW_MONTHS = 3;

function jurisdictionStaleness(entry: JurisdictionRiskEntry, internalReviewIntervalMonths: number): JurisdictionStalenessState {
  const months = monthsBetween(entry.lastReviewed);
  if (entry.sourceType === 'external') {
    if (months > EXTERNAL_STALE_MONTHS) return 'stale';
    if (months > EXTERNAL_NEARING_MONTHS) return 'nearing';
    return 'current';
  }
  if (months > internalReviewIntervalMonths) return 'stale';
  if (months > internalReviewIntervalMonths - INTERNAL_NEARING_WINDOW_MONTHS) return 'nearing';
  return 'current';
}

function anchorFor(riskValue: number) {
  return JURISDICTION_RISK_ANCHORS.find((a) => a.level === riskValue);
}

function StalenessBadge({ state }: { state: JurisdictionStalenessState }) {
  if (state === 'current') return <Badge tone="success">Current</Badge>;
  if (state === 'nearing')
    return (
      <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-[#B98A1E]">
        <Icon name="warning" size={13} /> Nearing
      </span>
    );
  return <Badge tone="danger">Stale</Badge>;
}

// -----------------------------------------------------------------------
// Anchor legend (§3) - written anchors for the 1-5 scale, on hover per row and here in full.
// -----------------------------------------------------------------------
function AnchorLegend({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <Card material="flat" className="p-4 flex flex-col gap-2.5">
      <button onClick={onToggle} className="flex items-center gap-2 text-left cursor-pointer">
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} className="text-text-faint shrink-0" />
        <span className="text-[13px] font-semibold text-text-primary">Anchor definitions for the 1-5 scale</span>
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 pl-6">
          {JURISDICTION_RISK_ANCHORS.map((a) => (
            <div key={a.level} className="flex items-start gap-2.5 text-[12.5px]">
              <span className="font-mono font-semibold text-text-primary w-6 shrink-0">{a.level}</span>
              <span className="font-semibold text-text-primary w-20 shrink-0">{a.label}</span>
              <span className="text-text-secondary">{a.definition}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// -----------------------------------------------------------------------
// Row - display and edit mode for one entry. External rows are locked (§4.1); internal rows are
// fully editable (§4.2/§4.3).
// -----------------------------------------------------------------------
function JurisdictionRow({
  e,
  editing,
  staleness,
  onStartEdit,
  onCancelEdit,
  onConfirmEdit,
  onRemove,
  error,
}: {
  e: JurisdictionRiskEntry;
  editing: boolean;
  staleness: JurisdictionStalenessState;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onConfirmEdit: (patch: { jurisdiction: string; riskValue: number; rationale: string }) => void;
  onRemove: () => void;
  error: string | null;
}) {
  const [jurisdiction, setJurisdiction] = useState(e.jurisdiction);
  const [riskValue, setRiskValue] = useState(e.riskValue);
  const [rationale, setRationale] = useState(e.rationale ?? '');

  useEffect(() => {
    if (editing) {
      setJurisdiction(e.jurisdiction);
      setRiskValue(e.riskValue);
      setRationale(e.rationale ?? '');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  const retired = !!e.retired;
  const locked = e.sourceType === 'external';

  if (editing) {
    return (
      <div className="flex flex-col gap-2.5 border border-carbon-moss rounded-md p-3.5 bg-white">
        <div className="grid grid-cols-[1.4fr_120px] gap-2.5">
          <Input value={jurisdiction} onChange={(ev) => setJurisdiction(ev.target.value)} placeholder="Jurisdiction" className="text-[13px] py-1.5" />
          <Select value={riskValue} onChange={(ev) => setRiskValue(Number(ev.target.value))} className="py-1.5 text-[12.5px]" title={anchorFor(riskValue)?.definition}>
            {JURISDICTION_RISK_ANCHORS.map((a) => (
              <option key={a.level} value={a.level}>
                {a.level} - {a.label}
              </option>
            ))}
          </Select>
        </div>
        <Textarea rows={2} value={rationale} onChange={(ev) => setRationale(ev.target.value)} placeholder="Rationale for this rating" />
        {error && (
          <div className="flex items-start gap-2 bg-danger-bg rounded-md px-3 py-2.5 text-[12.5px] text-danger">
            <Icon name="warning" size={14} className="shrink-0 mt-0.5" />
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2 pt-1 border-t border-border-hairline">
          <Button size="sm" variant="ghost" onClick={onCancelEdit}>
            Cancel
          </Button>
          <Button size="sm" variant="primary" disabled={!jurisdiction.trim()} onClick={() => onConfirmEdit({ jurisdiction: jurisdiction.trim(), riskValue, rationale: rationale.trim() })}>
            Confirm
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className={`grid grid-cols-[1.5fr_60px_1.7fr_110px_110px_auto] gap-3 items-center py-2.5 px-2 -mx-2 rounded-md text-[12.5px] ${retired ? 'opacity-50' : ''}`}>
      <div className="flex items-center gap-1.5 min-w-0">
        {locked && <Icon name="lock" size={12} className="text-text-faint shrink-0" />}
        <span className="text-text-primary font-medium truncate">{e.jurisdiction}</span>
      </div>
      <span title={anchorFor(e.riskValue)?.definition} className="font-mono font-semibold text-text-primary cursor-help">
        {e.riskValue}
      </span>
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-text-secondary truncate">{e.source}</span>
        {locked ? (
          <span className="text-[11px] text-text-faint truncate">
            {e.publisher} - {e.updateFrequency}
          </span>
        ) : (
          <span className="text-[11px] text-text-faint truncate">{e.rationale}</span>
        )}
      </div>
      <span className="text-[11.5px] text-text-faint">
        {locked ? 'Synced' : 'Reviewed'} {formatDate(e.lastReviewed)}
      </span>
      <div>{retired ? <Badge tone="default">Retired</Badge> : <StalenessBadge state={staleness} />}</div>
      <div className="flex items-center gap-1 justify-end">
        {locked ? (
          <span className="text-[11px] text-text-faint px-1.5">Admin-set, read-only</span>
        ) : retired ? (
          <span className="text-[11px] text-text-faint px-1.5">No further edits</span>
        ) : (
          <div className="flex items-center gap-1 border border-border-strong rounded-md p-1">
            <Button size="sm" variant="ghost" onClick={onStartEdit}>
              Edit
            </Button>
            <Button size="sm" variant="ghost" onClick={onRemove}>
              Remove
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function AddJurisdictionRow({ onAdd, onCancel, error }: { onAdd: (input: { jurisdiction: string; riskValue: number; rationale: string }) => void; onCancel: () => void; error: string | null }) {
  const [jurisdiction, setJurisdiction] = useState('');
  const [riskValue, setRiskValue] = useState(1);
  const [rationale, setRationale] = useState('');
  return (
    <div className="flex flex-col gap-2.5 border border-carbon-moss rounded-md p-3.5 bg-white">
      <div className="grid grid-cols-[1.4fr_120px] gap-2.5">
        <Input value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)} placeholder="Jurisdiction name" className="text-[13px] py-1.5" />
        <Select value={riskValue} onChange={(e) => setRiskValue(Number(e.target.value))} className="py-1.5 text-[12.5px]">
          {JURISDICTION_RISK_ANCHORS.map((a) => (
            <option key={a.level} value={a.level}>
              {a.level} - {a.label}
            </option>
          ))}
        </Select>
      </div>
      <Textarea rows={2} value={rationale} onChange={(e) => setRationale(e.target.value)} placeholder="Rationale for this rating" />
      <p className="text-[11.5px] text-text-faint">
        Added under Internal AML jurisdiction methodology. If this jurisdiction already carries an external listing, this entry may only raise its rating, never lower it.
      </p>
      {error && (
        <div className="flex items-start gap-2 bg-danger-bg rounded-md px-3 py-2.5 text-[12.5px] text-danger">
          <Icon name="warning" size={14} className="shrink-0 mt-0.5" />
          {error}
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" disabled={!jurisdiction.trim()} onClick={() => onAdd({ jurisdiction: jurisdiction.trim(), riskValue, rationale: rationale.trim() })}>
          Add
        </Button>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Import (§9) - admin-only, illustrative stand-in for a FATF/OFAC list file. No live connector is
// built; this is the stated production path.
// -----------------------------------------------------------------------
function ImportPreviewModal({
  open,
  diff,
  onClose,
  onConfirm,
  committing,
}: {
  open: boolean;
  diff: JurisdictionImportDiff | null;
  onClose: () => void;
  onConfirm: () => void;
  committing: boolean;
}) {
  return (
    <Modal open={open} onClose={onClose} title="Import preview - review before committing" width={580}>
      {diff && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Added ({diff.added.length})</span>
            {diff.added.length === 0 ? (
              <p className="text-[12.5px] text-text-faint">None.</p>
            ) : (
              diff.added.map((e) => (
                <div key={e.id} className="px-3 py-1.5 rounded-md bg-surface-tertiary text-[12.5px] text-text-secondary">
                  {e.jurisdiction} - {e.source}, risk {e.riskValue}
                </div>
              ))
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Changed ({diff.changed.length})</span>
            {diff.changed.length === 0 ? (
              <p className="text-[12.5px] text-text-faint">None.</p>
            ) : (
              diff.changed.map((c) => (
                <div key={c.id} className="px-3 py-1.5 rounded-md bg-surface-tertiary text-[12.5px] text-text-secondary flex flex-col gap-0.5">
                  <span className="font-medium text-text-primary">{c.jurisdiction}</span>
                  {c.fieldChanges.map((f, i) => (
                    <span key={i} className="text-[11.5px] text-text-faint">
                      {f}
                    </span>
                  ))}
                </div>
              ))
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Removed ({diff.removed.length})</span>
            {diff.removed.length === 0 ? (
              <p className="text-[12.5px] text-text-faint">None.</p>
            ) : (
              diff.removed.map((e) => (
                <div key={e.id} className="px-3 py-1.5 rounded-md bg-danger-bg text-[12.5px] text-danger">
                  {e.jurisdiction} - not in the imported list. Referenced entries retire; unreferenced entries are dropped.
                </div>
              ))
            )}
          </div>
          <p className="text-[11.5px] text-text-faint">Internal entries are never touched by an import - only external, admin-sourced entries are affected.</p>
          <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" onClick={onConfirm} loading={committing}>
              Confirm import
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

// -----------------------------------------------------------------------
// Impact preview (§10) - blocking, before any change commits.
// -----------------------------------------------------------------------
function ImpactModal({
  open,
  impact,
  pendingChangeCount,
  note,
  onNoteChange,
  onCancel,
  onConfirm,
  confirming,
}: {
  open: boolean;
  impact: JurisdictionRiskImpact | null;
  pendingChangeCount: number;
  note: string;
  onNoteChange: (v: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
  confirming: boolean;
}) {
  return (
    <Modal open={open} onClose={onCancel} title="Impact preview - review before publishing" width={620}>
      <div className="flex flex-col gap-3">
        <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3 py-2.5">
          <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
          <p className="text-[12px] text-[#133338]">
            {pendingChangeCount} pending change{pendingChangeCount === 1 ? '' : 's'} in this section.
          </p>
        </div>
        <div className="flex items-center justify-between px-3.5 py-2.5 rounded-md bg-surface-tertiary text-[12.5px]">
          <span className="text-text-secondary">Cases already scored (unaffected, stamped to their version)</span>
          <span className="font-mono font-semibold text-text-primary shrink-0 ml-3">{impact?.alreadyScoredCount ?? 0}</span>
        </div>
        <div className="flex items-center justify-between px-3.5 py-2.5 rounded-md bg-surface-tertiary text-[12.5px]">
          <span className="text-text-secondary">Cases pending an analysis run (will use the new value)</span>
          <span className="font-mono font-semibold text-text-primary shrink-0 ml-3">{impact?.pendingAnalysisRun.length ?? 0}</span>
        </div>
        {impact && impact.pendingAnalysisRun.length > 0 && (
          <div className="flex flex-col gap-1 pl-3.5">
            {impact.pendingAnalysisRun.map((c) => (
              <span key={c.caseId} className="text-[11.5px] text-text-faint">
                {c.caseId} - {c.title}
              </span>
            ))}
          </div>
        )}
        <div className="flex flex-col gap-1 px-3.5 py-2.5 rounded-md bg-surface-tertiary text-[12.5px]">
          <span className="text-text-secondary">Applied to the last {impact?.recomputation.evaluatedCount ?? 0} scored cases: would change band</span>
          <span className="font-mono font-semibold text-text-primary">{impact?.recomputation.changed.length ?? 0} case(s)</span>
        </div>
        {impact && impact.recomputation.changed.length > 0 && (
          <div className="flex flex-col gap-1 pl-3.5">
            {impact.recomputation.changed.map((c) => (
              <span key={c.caseId} className="text-[11.5px] text-text-faint">
                {c.caseId} - {c.title}: Band {c.fromBand} -&gt; Band {c.toBand}
              </span>
            ))}
          </div>
        )}
        <Textarea rows={2} placeholder="Version note (what changed and why)..." value={note} onChange={(e) => onNoteChange(e.target.value)} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onConfirm} loading={confirming}>
            Confirm and publish new version
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// -----------------------------------------------------------------------
// Main section
// -----------------------------------------------------------------------
type SortKey = 'riskValue' | 'lastReviewed';

export function JurisdictionRiskSection() {
  const { changes, addChange, clearSection, registerActions } = useWorkingSet();
  const toast = useToast();

  const [entries, setEntries] = useState<JurisdictionRiskEntry[]>([]);
  const [initialEntries, setInitialEntries] = useState<JurisdictionRiskEntry[]>([]);
  const [defaultUnlistedValue, setDefaultUnlistedValue] = useState(3);
  const [internalReviewIntervalMonths, setInternalReviewIntervalMonths] = useState(12);
  const [initialSettings, setInitialSettings] = useState({ defaultUnlistedValue: 3, internalReviewIntervalMonths: 12 });
  const [versions, setVersions] = useState<JurisdictionRiskListSnapshot[] | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<{ entry: JurisdictionRiskEntry; inUse: boolean } | null>(null);

  const [settingsEditing, setSettingsEditing] = useState(false);
  const [settingsBuffer, setSettingsBuffer] = useState({ defaultUnlistedValue: 3, internalReviewIntervalMonths: 12 });

  const [legendOpen, setLegendOpen] = useState(false);
  const [viewingVersion, setViewingVersion] = useState<number | null>(null);

  const [query, setQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState<'all' | JurisdictionSourceType>('all');
  const [riskFilter, setRiskFilter] = useState<'all' | number>('all');
  const [stalenessFilter, setStalenessFilter] = useState<'all' | JurisdictionStalenessState>('all');
  const [sortKey, setSortKey] = useState<SortKey>('riskValue');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const [importOpen, setImportOpen] = useState(false);
  const [importDiff, setImportDiff] = useState<JurisdictionImportDiff | null>(null);
  const [importPreviewing, setImportPreviewing] = useState(false);
  const [importCommitting, setImportCommitting] = useState(false);

  const [impact, setImpact] = useState<JurisdictionRiskImpact | null>(null);
  const [impactOpen, setImpactOpen] = useState(false);
  const [note, setNote] = useState('');
  const [publishing, setPublishing] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  async function reload() {
    const vers = await getJurisdictionRiskListVersions();
    setVersions(vers);
    const active = vers[vers.length - 1];
    setEntries(active.entries);
    setInitialEntries(active.entries);
    setDefaultUnlistedValue(active.defaultUnlistedValue);
    setInternalReviewIntervalMonths(active.internalReviewIntervalMonths);
    setInitialSettings({ defaultUnlistedValue: active.defaultUnlistedValue, internalReviewIntervalMonths: active.internalReviewIntervalMonths });
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function log(label: string, revert: () => void) {
    addChange({ label, sectionId: SECTION_ID, sectionLabel: SECTION_LABEL, revert });
  }

  const myChanges = changes.filter((c) => c.sectionId === SECTION_ID);

  const active = entries.filter((e) => !e.retired);
  const staleCount = active.filter((e) => jurisdictionStaleness(e, internalReviewIntervalMonths) === 'stale').length;

  const filtered = useMemo(() => {
    let rows = entries.filter((e) => e.jurisdiction.toLowerCase().includes(query.trim().toLowerCase()));
    if (sourceFilter !== 'all') rows = rows.filter((e) => e.sourceType === sourceFilter);
    if (riskFilter !== 'all') rows = rows.filter((e) => e.riskValue === riskFilter);
    if (stalenessFilter !== 'all') rows = rows.filter((e) => !e.retired && jurisdictionStaleness(e, internalReviewIntervalMonths) === stalenessFilter);
    rows = rows.slice().sort((a, b) => {
      const av = sortKey === 'riskValue' ? a.riskValue : new Date(a.lastReviewed).getTime();
      const bv = sortKey === 'riskValue' ? b.riskValue : new Date(b.lastReviewed).getTime();
      return sortDir === 'asc' ? av - bv : bv - av;
    });
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, query, sourceFilter, riskFilter, stalenessFilter, sortKey, sortDir, internalReviewIntervalMonths]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function confirmEdit(id: string, patch: { jurisdiction: string; riskValue: number; rationale: string }) {
    const prev = entries.find((e) => e.id === id);
    if (!prev) return;
    const candidate = entries.map((e) => (e.id === id ? { ...e, ...patch, lastReviewed: new Date().toISOString().slice(0, 10) } : e));
    const err = validateJurisdictionEntries(candidate);
    if (err) {
      setRowError(err);
      return;
    }
    setEntries(candidate);
    log(`Edited "${prev.jurisdiction}"`, () => setEntries((es) => es.map((e) => (e.id === id ? prev : e))));
    setEditingId(null);
    setRowError(null);
  }

  function addEntry(input: { jurisdiction: string; riskValue: number; rationale: string }) {
    const id = newId();
    const entry: JurisdictionRiskEntry = {
      id,
      jurisdiction: input.jurisdiction,
      riskValue: input.riskValue,
      source: 'Internal AML jurisdiction methodology',
      sourceType: 'internal',
      rationale: input.rationale,
      lastReviewed: new Date().toISOString().slice(0, 10),
    };
    const candidate = [...entries, entry];
    const err = validateJurisdictionEntries(candidate);
    if (err) {
      setRowError(err);
      return;
    }
    setEntries(candidate);
    log(`Added "${input.jurisdiction}"`, () => setEntries((es) => es.filter((e) => e.id !== id)));
    setAddOpen(false);
    setRowError(null);
  }

  async function requestRemove(entry: JurisdictionRiskEntry) {
    const inUse = await isJurisdictionInUse(entry.id);
    setRemoveTarget({ entry, inUse });
  }

  function confirmRemove() {
    if (!removeTarget) return;
    const { entry, inUse } = removeTarget;
    if (inUse) {
      setEntries((es) => es.map((e) => (e.id === entry.id ? { ...e, retired: true, retiredAt: new Date().toISOString().slice(0, 10) } : e)));
      log(`Retired "${entry.jurisdiction}"`, () => setEntries((es) => es.map((e) => (e.id === entry.id ? { ...e, retired: false, retiredAt: undefined } : e))));
    } else {
      setEntries((es) => es.filter((e) => e.id !== entry.id));
      log(`Deleted "${entry.jurisdiction}"`, () => setEntries((es) => [...es, entry]));
    }
    setRemoveTarget(null);
  }

  function confirmSettings() {
    const prev = { defaultUnlistedValue, internalReviewIntervalMonths };
    setDefaultUnlistedValue(settingsBuffer.defaultUnlistedValue);
    setInternalReviewIntervalMonths(settingsBuffer.internalReviewIntervalMonths);
    setSettingsEditing(false);
    if (prev.defaultUnlistedValue !== settingsBuffer.defaultUnlistedValue || prev.internalReviewIntervalMonths !== settingsBuffer.internalReviewIntervalMonths) {
      log('Updated default and review interval settings', () => {
        setDefaultUnlistedValue(prev.defaultUnlistedValue);
        setInternalReviewIntervalMonths(prev.internalReviewIntervalMonths);
      });
    }
  }

  async function reviewImpact() {
    const err = validateJurisdictionEntries(entries);
    if (err) {
      toast(err, 'danger');
      return;
    }
    const result = await previewJurisdictionRiskImpact(entries, defaultUnlistedValue);
    setImpact(result);
    setValidationError(null);
    setImpactOpen(true);
  }

  async function handlePublish() {
    setPublishing(true);
    const result = await commitJurisdictionRiskListChange(entries, defaultUnlistedValue, internalReviewIntervalMonths, note.trim() || 'Jurisdiction Risk List update.');
    setPublishing(false);
    if ('error' in result) {
      setValidationError(result.error);
      return;
    }
    setImpactOpen(false);
    setNote('');
    clearSection(SECTION_ID);
    toast('New Jurisdiction Risk List version published.', 'success');
    reload();
  }

  function handleDiscard() {
    setEntries(initialEntries);
    setDefaultUnlistedValue(initialSettings.defaultUnlistedValue);
    setInternalReviewIntervalMonths(initialSettings.internalReviewIntervalMonths);
    setEditingId(null);
    setSettingsEditing(false);
    clearSection(SECTION_ID);
  }

  useEffect(() => {
    registerActions(SECTION_ID, { publish: reviewImpact, discard: handleDiscard });
  });

  async function runImportPreview() {
    setImportPreviewing(true);
    const diff = await previewJurisdictionImport();
    setImportPreviewing(false);
    setImportDiff(diff);
    setImportOpen(true);
  }

  async function confirmImport() {
    if (!importDiff) return;
    setImportCommitting(true);
    await commitJurisdictionImport(importDiff, 'FATF/OFAC illustrative sync (§9)');
    setImportCommitting(false);
    setImportOpen(false);
    toast('Import committed as a new Jurisdiction Risk List version.', 'success');
    reload();
  }

  if (!versions) return <Skeleton className="h-96 rounded-md" />;

  const activeVersionNumber = versions[versions.length - 1].version;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-relaxed">
        <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
        <p>
          <strong>Provenance for geography scoring.</strong> Without this list, the likelihood value on every geography assessment is an analyst's individual opinion about a country - the
          "two analysts, two conclusions" problem the product exists to solve. The <strong>highest-risk in-scope jurisdiction sets the baseline geography likelihood</strong> (impact is
          untouched - that's about consequence severity, not geography). An analyst may adjust the baseline with a logged reason, visible in the case's Risk &amp; Controls drill-down as{' '}
          <em>"Jurisdiction risk list, adjusted by analyst."</em> The Risk Drafting Agent pulls from this list, and every provenance label resolves here.
        </p>
      </div>

      <div className="flex items-start gap-2.5 bg-[#FBF3E4] rounded-md px-4 py-3 text-[12.5px] text-[#5c4415] leading-relaxed">
        <Icon name="warning" size={14} className="text-[#B98A1E] shrink-0 mt-0.5" />
        <p>
          <strong>Verified as of the June 2026 FATF plenary and current OFAC programs.</strong> A jurisdiction's grey-list status moves between plenaries, so re-verify against the current
          publications before this goes in front of a client - the next plenary may already have changed what's listed here.
        </p>
      </div>

      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3 text-[12.5px] text-text-secondary">
          <span>
            {filtered.length} of {entries.length} shown
          </span>
          {staleCount > 0 && (
            <span className="inline-flex items-center gap-1.5 font-semibold text-danger">
              <Icon name="warning" size={13} /> {staleCount} stale
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-text-faint">Admin action -</span>
          <Button size="sm" variant="secondary" icon={<Icon name="upload" size={13} />} onClick={runImportPreview} loading={importPreviewing}>
            Preview FATF/OFAC sync
          </Button>
        </div>
      </div>

      <AnchorLegend open={legendOpen} onToggle={() => setLegendOpen((o) => !o)} />

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <span className="text-sm font-bold text-text-primary">Entries</span>
          <div className="flex items-center gap-2 flex-wrap">
            <Input placeholder="Search jurisdiction..." value={query} onChange={(e) => setQuery(e.target.value)} className="w-[180px] py-1.5 text-[12.5px]" />
            <Select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value as typeof sourceFilter)} className="py-1.5 text-[12.5px]">
              <option value="all">All sources</option>
              <option value="external">External</option>
              <option value="internal">Internal</option>
            </Select>
            <Select value={riskFilter === 'all' ? 'all' : String(riskFilter)} onChange={(e) => setRiskFilter(e.target.value === 'all' ? 'all' : Number(e.target.value))} className="py-1.5 text-[12.5px]">
              <option value="all">All risk values</option>
              {JURISDICTION_RISK_ANCHORS.map((a) => (
                <option key={a.level} value={a.level}>
                  {a.level} - {a.label}
                </option>
              ))}
            </Select>
            <Select value={stalenessFilter} onChange={(e) => setStalenessFilter(e.target.value as typeof stalenessFilter)} className="py-1.5 text-[12.5px]">
              <option value="all">All staleness</option>
              <option value="current">Current</option>
              <option value="nearing">Nearing</option>
              <option value="stale">Stale</option>
            </Select>
          </div>
        </div>

        <div className="max-h-[520px] overflow-y-auto flex flex-col gap-1">
          <div className="sticky top-0 bg-white z-10 grid grid-cols-[1.5fr_60px_1.7fr_110px_110px_auto] gap-3 px-2 pb-2 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint border-b border-border-hairline">
            <span>Jurisdiction</span>
            <button onClick={() => toggleSort('riskValue')} className="text-left cursor-pointer hover:text-text-secondary flex items-center gap-0.5">
              Risk <Icon name={sortKey === 'riskValue' ? (sortDir === 'asc' ? 'chevron-right' : 'chevron-down') : 'chevron-down'} size={10} />
            </button>
            <span>Source</span>
            <button onClick={() => toggleSort('lastReviewed')} className="text-left cursor-pointer hover:text-text-secondary flex items-center gap-0.5">
              Reviewed <Icon name={sortKey === 'lastReviewed' ? (sortDir === 'asc' ? 'chevron-right' : 'chevron-down') : 'chevron-down'} size={10} />
            </button>
            <span>Staleness</span>
            <span className="text-right">Manage</span>
          </div>
          {filtered.length === 0 ? (
            <p className="text-[12px] text-text-faint px-2 py-3">No entries match.</p>
          ) : (
            filtered.map((e) => (
              <JurisdictionRow
                key={e.id}
                e={e}
                editing={editingId === e.id}
                staleness={jurisdictionStaleness(e, internalReviewIntervalMonths)}
                onStartEdit={() => {
                  setEditingId(e.id);
                  setRowError(null);
                }}
                onCancelEdit={() => {
                  setEditingId(null);
                  setRowError(null);
                }}
                onConfirmEdit={(patch) => confirmEdit(e.id, patch)}
                onRemove={() => requestRemove(e)}
                error={editingId === e.id ? rowError : null}
              />
            ))
          )}
        </div>

        {addOpen ? (
          <AddJurisdictionRow onAdd={addEntry} onCancel={() => setAddOpen(false)} error={rowError} />
        ) : (
          <Button size="sm" variant="ghost" className="self-start" icon={<Icon name="plus" size={13} />} onClick={() => setAddOpen(true)}>
            Add internal entry
          </Button>
        )}
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Default and review settings</span>
          {!settingsEditing && (
            <Button size="sm" variant="ghost" icon={<Icon name="edit" size={13} />} onClick={() => { setSettingsBuffer({ defaultUnlistedValue, internalReviewIntervalMonths }); setSettingsEditing(true); }}>
              Edit
            </Button>
          )}
        </div>
        <div className="grid grid-cols-2 gap-5">
          <div className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Default for an unlisted jurisdiction</span>
            {settingsEditing ? (
              <Select value={settingsBuffer.defaultUnlistedValue} onChange={(e) => setSettingsBuffer((s) => ({ ...s, defaultUnlistedValue: Number(e.target.value) }))} className="w-40">
                {JURISDICTION_RISK_ANCHORS.map((a) => (
                  <option key={a.level} value={a.level}>
                    {a.level} - {a.label}
                  </option>
                ))}
              </Select>
            ) : (
              <span className="font-mono text-[14px] font-semibold text-text-primary">
                {defaultUnlistedValue} - {anchorFor(defaultUnlistedValue)?.label}
              </span>
            )}
            <span className="text-[12px] text-text-muted">Never applied silently - a case naming an unlisted jurisdiction is flagged on Risk &amp; Controls.</span>
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Internal review interval (months)</span>
            {settingsEditing ? (
              <Input type="number" min={1} value={settingsBuffer.internalReviewIntervalMonths} onChange={(e) => setSettingsBuffer((s) => ({ ...s, internalReviewIntervalMonths: Number(e.target.value) }))} className="w-24" />
            ) : (
              <span className="font-mono text-[14px] font-semibold text-text-primary">{internalReviewIntervalMonths}</span>
            )}
            <span className="text-[12px] text-text-muted">External entries use FATF's own plenary cycle instead - nearing after one cycle, stale after two.</span>
          </div>
        </div>
        {settingsEditing && (
          <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
            <Button size="sm" variant="ghost" onClick={() => setSettingsEditing(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={confirmSettings}>
              Confirm
            </Button>
          </div>
        )}
      </Card>

      <div className="flex items-start gap-2.5 bg-surface-tertiary rounded-md px-4 py-3 text-[12px] text-text-secondary leading-relaxed">
        <Icon name="info" size={14} className="text-text-muted shrink-0 mt-0.5" />
        <p>
          <strong>Not change-type dependent, by design.</strong> A jurisdiction's AML risk is a property of the jurisdiction, not of what's changing there - trigger categories already decide
          which AML categories are in scope, and likelihood/impact already capture the nature of the exposure. Varying this value by change type would double-count the same factor, and is
          unmaintainable at roughly 200 jurisdictions across six triggers. If change-type sensitivity proves genuinely necessary, the right place is a small modifier layer in the scoring
          model, not a larger jurisdiction table.
        </p>
      </div>

      <div className="flex items-start gap-2.5 bg-surface-tertiary rounded-md px-4 py-3 text-[12px] text-text-secondary leading-relaxed">
        <Icon name="info" size={14} className="text-text-muted shrink-0 mt-0.5" />
        <p>
          <strong>On roles.</strong> External entries are admin-set: a platform or reference-data owner, not a reviewer of analyst work, and a different thing from the lead or approver role
          deliberately deferred elsewhere in this build. Analysts still configure everything else in their own workspace - externally-published reference data simply isn't theirs to edit.
        </p>
      </div>

      <Card material="flat" className="p-5 flex flex-col gap-2">
        <span className="text-sm font-bold text-text-primary">Version history</span>
        <div className="flex flex-col divide-y divide-border-hairline">
          {versions
            .slice()
            .reverse()
            .map((v) => (
              <div key={v.version} className="py-2.5 flex flex-col gap-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-[12.5px] font-semibold text-text-primary">v{v.version}</span>
                  {v.version === activeVersionNumber && <Badge tone="success">Active</Badge>}
                  <span className="text-[11.5px] text-text-faint">{formatDate(v.publishedAt)}</span>
                  <span className="text-[11.5px] text-text-faint">{v.changedBy}</span>
                  <button
                    onClick={() => setViewingVersion((cur) => (cur === v.version ? null : v.version))}
                    className="ml-auto text-[11.5px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer"
                  >
                    {viewingVersion === v.version ? 'Hide detail' : 'View detail'}
                  </button>
                </div>
                <p className="text-[12.5px] text-text-secondary leading-relaxed">{v.note}</p>
                {viewingVersion === v.version && (
                  <div className="flex flex-col gap-1 mt-1 p-3 rounded-md bg-surface-tertiary text-[12px]">
                    <div className="flex justify-between">
                      <span className="text-text-faint">Entries</span>
                      <span className="font-mono font-semibold text-text-primary">{v.entries.length}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-text-faint">Default for unlisted</span>
                      <span className="font-mono font-semibold text-text-primary">{v.defaultUnlistedValue}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-text-faint">Internal review interval</span>
                      <span className="font-mono font-semibold text-text-primary">{v.internalReviewIntervalMonths} months</span>
                    </div>
                  </div>
                )}
              </div>
            ))}
        </div>
      </Card>

      <Modal open={!!removeTarget} onClose={() => setRemoveTarget(null)} title={removeTarget?.inUse ? 'Retire entry' : 'Delete entry'} width={460}>
        {removeTarget && (
          <div className="flex flex-col gap-4">
            {removeTarget.inUse ? (
              <p className="text-[13px] text-text-secondary">
                <strong>"{removeTarget.entry.jurisdiction}"</strong> is referenced by at least one assessed case. It will be <strong>retired</strong>: unavailable to new assessments, but
                still rendering on the cases that already used it.
              </p>
            ) : (
              <p className="text-[13px] text-text-secondary">
                <strong>"{removeTarget.entry.jurisdiction}"</strong> has never been used in an assessed case. It can be <strong>deleted</strong> outright.
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setRemoveTarget(null)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={confirmRemove}>
                {removeTarget.inUse ? 'Retire' : 'Delete'}
              </Button>
            </div>
          </div>
        )}
      </Modal>

      {validationError && (
        <div className="flex items-start gap-2 bg-danger-bg rounded-md px-3.5 py-3 text-[12.5px] text-danger">
          <Icon name="warning" size={14} className="shrink-0 mt-0.5" />
          {validationError}
        </div>
      )}

      <ImpactModal open={impactOpen} impact={impact} pendingChangeCount={myChanges.length} note={note} onNoteChange={setNote} onCancel={() => setImpactOpen(false)} onConfirm={handlePublish} confirming={publishing} />
      <ImportPreviewModal open={importOpen} diff={importDiff} onClose={() => setImportOpen(false)} onConfirm={confirmImport} committing={importCommitting} />
    </div>
  );
}
