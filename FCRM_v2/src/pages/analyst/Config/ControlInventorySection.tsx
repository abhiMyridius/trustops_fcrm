import { useEffect, useState } from 'react';
import type { AmlCategory, ControlInventoryItem, ControlStalenessState, EvidenceType, InventoryEvidence, RoutingRules } from '@/types/analyst';
import { ALL_AML_CATEGORIES, EVIDENCE_TYPES } from '@/types/analyst';
import {
  commitControlImport,
  commitControlInventoryChange,
  getControlInventory,
  getControlInventoryVersions,
  getRoutingRules,
  isControlInUse,
  previewControlImport,
  validateControlWeights,
  type ControlImportDiff,
  type ControlImportRow,
  type ControlWeightGap,
} from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge, Tag } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Select, Textarea } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/utils/format';
import { useWorkingSet } from './WorkingSetContext';

const SECTION_ID = 'controls';
const SECTION_LABEL = 'Control Inventory';

function newId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function monthsBetween(dateStr: string): number {
  const d = new Date(dateStr);
  const now = new Date();
  return (now.getFullYear() - d.getFullYear()) * 12 + (now.getMonth() - d.getMonth());
}

function controlStaleness(evidence: InventoryEvidence, recencyMonths: number, nearingMonths: number): ControlStalenessState {
  if (evidence.type === 'None' || !evidence.date) return 'judgment-only';
  const months = monthsBetween(evidence.date);
  if (months > recencyMonths) return 'stale';
  if (months > recencyMonths - nearingMonths) return 'nearing';
  return 'current';
}

function categoryWeightTotal(controls: ControlInventoryItem[], category: AmlCategory): number {
  return Math.round(controls.filter((c) => c.status === 'active' && c.category === category).reduce((s, c) => s + c.weight, 0) * 100) / 100;
}

const BLANK_EVIDENCE: InventoryEvidence = { name: '', type: 'None', link: null, date: null };

// -----------------------------------------------------------------------
// Row - display and edit mode for one control
// -----------------------------------------------------------------------
function ControlRow({
  c,
  editing,
  recencyMonths,
  nearingMonths,
  onStartEdit,
  onCancelEdit,
  onConfirmEdit,
  onRemove,
}: {
  c: ControlInventoryItem;
  editing: boolean;
  recencyMonths: number;
  nearingMonths: number;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onConfirmEdit: (patch: { name: string; weight: number; effectiveness: number; evidence: InventoryEvidence }) => void;
  onRemove: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(c.name);
  const [weight, setWeight] = useState(c.weight);
  const [effectiveness, setEffectiveness] = useState(c.effectiveness);
  const [evName, setEvName] = useState(c.evidence.name);
  const [evType, setEvType] = useState<EvidenceType>(c.evidence.type);
  const [evLink, setEvLink] = useState(c.evidence.link ?? '');
  const [evDate, setEvDate] = useState(c.evidence.date ?? '');

  useEffect(() => {
    if (editing) {
      setName(c.name);
      setWeight(c.weight);
      setEffectiveness(c.effectiveness);
      setEvName(c.evidence.name);
      setEvType(c.evidence.type);
      setEvLink(c.evidence.link ?? '');
      setEvDate(c.evidence.date ?? '');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  const state = controlStaleness(c.evidence, recencyMonths, nearingMonths);
  const retired = c.status === 'retired';

  function displayAction() {
    if (state === 'judgment-only') {
      return (
        <Button size="sm" variant="ghost" onClick={() => toast('Add evidence - this affordance is display-only in this build; no workflow is triggered.', 'default')}>
          Add evidence
        </Button>
      );
    }
    if (state === 'nearing' || state === 'stale') {
      return (
        <Button size="sm" variant="ghost" onClick={() => toast('Retest requested - this affordance is display-only in this build; no workflow is triggered.', 'default')}>
          Request retest
        </Button>
      );
    }
    return <span className="text-[11.5px] text-text-faint">-</span>;
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-2.5 border border-carbon-moss rounded-md p-3.5 bg-white">
        <div className="grid grid-cols-3 gap-2.5">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Control name" className="col-span-3 text-[13px] py-1.5" />
          <div className="flex flex-col gap-1">
            <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Weight</label>
            <Input type="number" min={0} step={0.05} value={weight} onChange={(e) => setWeight(Number(e.target.value))} className="py-1.5 text-[12.5px]" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">CE (0-10)</label>
            <Input type="number" min={0} max={10} step={0.1} value={effectiveness} onChange={(e) => setEffectiveness(Number(e.target.value))} className="py-1.5 text-[12.5px]" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Evidence type</label>
            <Select value={evType} onChange={(e) => setEvType(e.target.value as EvidenceType)} className="py-1.5 text-[12.5px]">
              {EVIDENCE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </div>
        </div>
        {evType !== 'None' && (
          <div className="grid grid-cols-3 gap-2.5">
            <div className="flex flex-col gap-1">
              <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Artifact name</label>
              <Input value={evName} onChange={(e) => setEvName(e.target.value)} className="py-1.5 text-[12.5px]" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Link</label>
              <Input value={evLink} onChange={(e) => setEvLink(e.target.value)} placeholder="https://..." className="py-1.5 text-[12.5px]" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Tested date</label>
              <Input type="date" value={evDate} onChange={(e) => setEvDate(e.target.value)} className="py-1.5 text-[12.5px]" />
            </div>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-1 border-t border-border-hairline">
          <Button size="sm" variant="ghost" onClick={onCancelEdit}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!name.trim()}
            onClick={() =>
              onConfirmEdit({
                name: name.trim(),
                weight,
                effectiveness,
                evidence: evType === 'None' ? BLANK_EVIDENCE : { name: evName.trim(), type: evType, link: evLink.trim() || null, date: evDate || null },
              })
            }
          >
            Confirm
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className={`grid grid-cols-[1.6fr_70px_60px_1.6fr_110px_110px_auto] gap-3 items-center py-2.5 px-2 -mx-2 rounded-md text-[12.5px] ${retired ? 'opacity-50' : ''}`}>
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-text-primary font-medium truncate">{c.name}</span>
        {c.source !== 'Seed' && <Tag className="self-start text-[10.5px] px-1.5 py-0.5">{c.source}</Tag>}
      </div>
      <span className="font-mono text-text-secondary">{c.weight.toFixed(2)}</span>
      <span className="font-mono text-text-secondary">{c.effectiveness.toFixed(1)}</span>
      <div className="flex flex-col gap-0.5 min-w-0">
        {c.evidence.type === 'None' ? (
          <span className="text-text-faint italic">Judgment only, no evidence</span>
        ) : (
          <>
            <span className="text-text-secondary truncate">
              {c.evidence.name} &middot; {c.evidence.type}
            </span>
            <div className="flex items-center gap-2 text-[11px] text-text-faint">
              {c.evidence.link && (
                <a href={c.evidence.link} target="_blank" rel="noopener noreferrer" className="text-carbon-moss hover:text-carbon-moss-hover underline inline-flex items-center gap-0.5">
                  <Icon name="external-link" size={10} /> link
                </a>
              )}
              <span>Tested {c.evidence.date ? formatDate(c.evidence.date) : 'Not tested'}</span>
            </div>
          </>
        )}
      </div>
      <div>
        {retired ? (
          <Badge tone="default">Retired</Badge>
        ) : state === 'current' ? (
          <Badge tone="success">Current</Badge>
        ) : state === 'nearing' ? (
          <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-[#B98A1E]">
            <Icon name="warning" size={13} /> Nearing
          </span>
        ) : state === 'stale' ? (
          <Badge tone="danger">Stale</Badge>
        ) : (
          <Badge tone="default">Judgment only</Badge>
        )}
      </div>
      <div>{!retired && displayAction()}</div>
      <div className="flex items-center gap-1 border border-border-strong rounded-md p-1">
        {retired ? (
          <span className="text-[11px] text-text-faint px-1.5">No further edits</span>
        ) : (
          <>
            <Button size="sm" variant="ghost" onClick={onStartEdit}>
              Edit
            </Button>
            <Button size="sm" variant="ghost" onClick={onRemove}>
              Retire
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function AddControlRow({ category, onAdd, onCancel }: { category: AmlCategory; onAdd: (input: { name: string; weight: number; effectiveness: number }) => void; onCancel: () => void }) {
  const [name, setName] = useState('');
  const [weight, setWeight] = useState(0);
  const [effectiveness, setEffectiveness] = useState(5);
  return (
    <div className="flex flex-col gap-2.5 border border-carbon-moss rounded-md p-3.5 bg-white">
      <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={`New control for ${category}...`} className="text-[13px] py-1.5" />
      <div className="grid grid-cols-2 gap-2.5">
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Weight</label>
          <Input type="number" min={0} step={0.05} value={weight} onChange={(e) => setWeight(Number(e.target.value))} className="py-1.5 text-[12.5px]" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">CE (0-10)</label>
          <Input type="number" min={0} max={10} step={0.1} value={effectiveness} onChange={(e) => setEffectiveness(Number(e.target.value))} className="py-1.5 text-[12.5px]" />
        </div>
      </div>
      <p className="text-[11.5px] text-text-faint">Evidence starts as judgment-only - edit the row afterward to attach it.</p>
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" disabled={!name.trim()} onClick={() => onAdd({ name: name.trim(), weight, effectiveness })}>
          Add
        </Button>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Import wizard - upload, column mapping, blocking preview
// -----------------------------------------------------------------------
const TARGET_FIELDS: { key: keyof ControlImportRow; label: string; required: boolean }[] = [
  { key: 'name', label: 'Control name', required: true },
  { key: 'category', label: 'AML category', required: true },
  { key: 'weight', label: 'Weight', required: true },
  { key: 'effectiveness', label: 'Control effectiveness (CE)', required: true },
  { key: 'evidenceName', label: 'Evidence artifact name', required: false },
  { key: 'evidenceType', label: 'Evidence type', required: false },
  { key: 'evidenceLink', label: 'Evidence link', required: false },
  { key: 'evidenceDate', label: 'Evidence tested date', required: false },
];

function parseCsv(text: string): { headers: string[]; rows: string[][] } {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  function splitLine(line: string): string[] {
    const cells: string[] = [];
    let cur = '';
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (inQuotes) {
        if (ch === '"' && line[i + 1] === '"') {
          cur += '"';
          i++;
        } else if (ch === '"') inQuotes = false;
        else cur += ch;
      } else if (ch === '"') inQuotes = true;
      else if (ch === ',') {
        cells.push(cur);
        cur = '';
      } else cur += ch;
    }
    cells.push(cur);
    return cells.map((c) => c.trim());
  }
  if (lines.length === 0) return { headers: [], rows: [] };
  return { headers: splitLine(lines[0]), rows: lines.slice(1).map(splitLine) };
}

function ImportWizard({ open, onClose, onCommitted }: { open: boolean; onClose: () => void; onCommitted: (version: number) => void }) {
  const toast = useToast();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [fileName, setFileName] = useState('');
  const [unsupported, setUnsupported] = useState(false);
  const [headers, setHeaders] = useState<string[]>([]);
  const [rows, setRows] = useState<string[][]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [diff, setDiff] = useState<ControlImportDiff | null>(null);
  const [keepConflicts, setKeepConflicts] = useState<Set<string>>(new Set());
  const [committing, setCommitting] = useState(false);

  useEffect(() => {
    if (open) {
      setStep(1);
      setFileName('');
      setUnsupported(false);
      setHeaders([]);
      setRows([]);
      setMapping({});
      setDiff(null);
      setKeepConflicts(new Set());
    }
  }, [open]);

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    if (!file.name.toLowerCase().endsWith('.csv')) {
      setUnsupported(true);
      return;
    }
    setUnsupported(false);
    const reader = new FileReader();
    reader.onload = () => {
      const { headers: h, rows: r } = parseCsv(String(reader.result ?? ''));
      setHeaders(h);
      setRows(r);
      const guess: Record<string, string> = {};
      for (const f of TARGET_FIELDS) {
        const match = h.find((x) => x.toLowerCase().replace(/[^a-z0-9]/g, '') === f.key.toLowerCase().replace(/[^a-z0-9]/g, ''));
        if (match) guess[f.key] = match;
      }
      setMapping(guess);
      setStep(2);
    };
    reader.readAsText(file);
  }

  function buildRows(): ControlImportRow[] {
    const idx = (col: string) => headers.indexOf(col);
    const nameIdx = idx(mapping.name);
    const catIdx = idx(mapping.category);
    const weightIdx = idx(mapping.weight);
    const ceIdx = idx(mapping.effectiveness);
    const evNameIdx = mapping.evidenceName ? idx(mapping.evidenceName) : -1;
    const evTypeIdx = mapping.evidenceType ? idx(mapping.evidenceType) : -1;
    const evLinkIdx = mapping.evidenceLink ? idx(mapping.evidenceLink) : -1;
    const evDateIdx = mapping.evidenceDate ? idx(mapping.evidenceDate) : -1;

    const out: ControlImportRow[] = [];
    for (const r of rows) {
      const name = r[nameIdx]?.trim();
      const catRaw = r[catIdx]?.trim() ?? '';
      const category = ALL_AML_CATEGORIES.find((c) => c.toLowerCase() === catRaw.toLowerCase());
      const weight = Number(r[weightIdx]);
      const effectiveness = Number(r[ceIdx]);
      if (!name || !category || Number.isNaN(weight) || Number.isNaN(effectiveness)) continue;
      const evTypeRaw = evTypeIdx >= 0 ? r[evTypeIdx]?.trim() : '';
      const evidenceType = EVIDENCE_TYPES.find((t) => t.toLowerCase() === evTypeRaw?.toLowerCase());
      out.push({
        name,
        category,
        weight,
        effectiveness,
        evidenceName: evNameIdx >= 0 ? r[evNameIdx] : undefined,
        evidenceType,
        evidenceLink: evLinkIdx >= 0 ? r[evLinkIdx] : undefined,
        evidenceDate: evDateIdx >= 0 ? r[evDateIdx] : undefined,
      });
    }
    return out;
  }

  async function goToPreview() {
    const importRows = buildRows();
    if (importRows.length === 0) {
      toast('No usable rows found - check the column mapping.', 'danger');
      return;
    }
    const result = await previewControlImport(importRows);
    setDiff(result);
    setKeepConflicts(new Set(result.conflicts.map((c) => c.id)));
    setStep(3);
  }

  async function confirmImport() {
    if (!diff) return;
    setCommitting(true);
    const result = await commitControlImport(diff, Array.from(keepConflicts), fileName);
    setCommitting(false);
    if ('error' in result) {
      toast(`Import blocked - ${result.error.map((g) => `${g.category} sums to ${g.total.toFixed(2)}`).join('; ')}.`, 'danger');
      return;
    }
    onCommitted(result.version);
    onClose();
  }

  const requiredMapped = TARGET_FIELDS.filter((f) => f.required).every((f) => mapping[f.key]);

  return (
    <Modal open={open} onClose={onClose} title="Import control inventory" width={640}>
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3.5 py-3">
          <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
          <p className="text-[12px] text-[#133338] leading-relaxed">
            This build parses CSV directly - export your GRC platform's register (Archer, ServiceNow IRM, MetricStream) or spreadsheet to CSV first. A live connector is the production path
            for keeping this current; it is not built here.
          </p>
        </div>

        {step === 1 && (
          <div className="flex flex-col gap-3">
            <input type="file" accept=".csv,.xlsx,.xls" onChange={handleFile} className="text-[13px]" />
            {unsupported && <p className="text-[12.5px] text-danger">This build parses CSV directly. Export the Excel sheet to CSV and upload that instead.</p>}
          </div>
        )}

        {step === 2 && (
          <div className="flex flex-col gap-3">
            <p className="text-[12.5px] text-text-muted">Map {fileName}'s columns to the fields Control Inventory uses. Evidence fields are optional.</p>
            <div className="flex flex-col gap-2 max-h-[360px] overflow-y-auto">
              {TARGET_FIELDS.map((f) => (
                <div key={f.key} className="flex items-center gap-3">
                  <span className="text-[12.5px] text-text-secondary flex-1">
                    {f.label}
                    {f.required && <span className="text-danger"> *</span>}
                  </span>
                  <Select value={mapping[f.key] ?? ''} onChange={(e) => setMapping((m) => ({ ...m, [f.key]: e.target.value }))} className="w-[220px] py-1.5 text-[12.5px]">
                    <option value="">Not mapped</option>
                    {headers.map((h) => (
                      <option key={h} value={h}>
                        {h}
                      </option>
                    ))}
                  </Select>
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
              <Button variant="ghost" onClick={() => setStep(1)}>
                Back
              </Button>
              <Button variant="primary" disabled={!requiredMapped} onClick={goToPreview}>
                Preview import
              </Button>
            </div>
          </div>
        )}

        {step === 3 && diff && (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Added ({diff.added.length})</span>
              {diff.added.length === 0 ? (
                <p className="text-[12.5px] text-text-faint">None.</p>
              ) : (
                diff.added.map((c) => (
                  <div key={c.id} className="px-3 py-1.5 rounded-md bg-surface-tertiary text-[12.5px] text-text-secondary">
                    {c.name} - {c.category} - weight {c.weight.toFixed(2)}
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
                    <span className="font-medium text-text-primary">{c.name}</span>
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
                diff.removed.map((c) => (
                  <div key={c.id} className="px-3 py-1.5 rounded-md bg-danger-bg text-[12.5px] text-danger">
                    {c.name} - not in the imported file
                  </div>
                ))
              )}
            </div>
            {diff.conflicts.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Analyst-added, not in this file - your call</span>
                {diff.conflicts.map((c) => (
                  <label key={c.id} className="flex items-center gap-2.5 px-3 py-1.5 rounded-md bg-[#FBF3E4] text-[12.5px] cursor-pointer">
                    <input
                      type="checkbox"
                      checked={keepConflicts.has(c.id)}
                      onChange={(e) =>
                        setKeepConflicts((s) => {
                          const next = new Set(s);
                          if (e.target.checked) next.add(c.id);
                          else next.delete(c.id);
                          return next;
                        })
                      }
                    />
                    <span className="flex-1 text-text-secondary">{c.name}</span>
                    <span className="text-[11px] text-text-faint">{keepConflicts.has(c.id) ? 'Keep' : 'Remove'}</span>
                  </label>
                ))}
              </div>
            )}
            <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
              <Button variant="ghost" onClick={() => setStep(2)}>
                Back
              </Button>
              <Button variant="primary" onClick={confirmImport} loading={committing}>
                Confirm import
              </Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

// -----------------------------------------------------------------------
// Main section
// -----------------------------------------------------------------------
export function ControlInventorySection() {
  const { changes, addChange, clearSection, registerActions } = useWorkingSet();
  const toast = useToast();

  const [controls, setControls] = useState<ControlInventoryItem[]>([]);
  const [initial, setInitial] = useState<ControlInventoryItem[]>([]);
  const [versions, setVersions] = useState<{ version: number; publishedAt: string; note: string; changedBy: string }[] | null>(null);
  const [routingRules, setRoutingRules] = useState<RoutingRules | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [addOpenFor, setAddOpenFor] = useState<AmlCategory | null>(null);
  const [removeTarget, setRemoveTarget] = useState<{ item: ControlInventoryItem; inUse: boolean } | null>(null);
  const [importOpen, setImportOpen] = useState(false);

  const [impactOpen, setImpactOpen] = useState(false);
  const [note, setNote] = useState('');
  const [publishing, setPublishing] = useState(false);
  const [weightGaps, setWeightGaps] = useState<ControlWeightGap[]>([]);

  async function reload() {
    const [c, vers, routing] = await Promise.all([getControlInventory(), getControlInventoryVersions(), getRoutingRules()]);
    setControls(c);
    setInitial(c);
    setVersions(vers);
    setRoutingRules(routing);
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function log(label: string, revert: () => void) {
    addChange({ label, sectionId: SECTION_ID, sectionLabel: SECTION_LABEL, revert });
  }

  function handleReviewImpact() {
    setWeightGaps(validateControlWeights(controls));
    setImpactOpen(true);
  }

  async function handlePublish() {
    setPublishing(true);
    const result = await commitControlInventoryChange(controls, note.trim() || 'Control inventory update.');
    setPublishing(false);
    if ('error' in result) {
      setWeightGaps(result.error);
      return;
    }
    setWeightGaps([]);
    setImpactOpen(false);
    setNote('');
    clearSection(SECTION_ID);
    toast('New control inventory version published.', 'success');
    reload();
  }

  function handleDiscard() {
    setControls(initial);
    clearSection(SECTION_ID);
  }

  useEffect(() => {
    registerActions(SECTION_ID, { publish: handleReviewImpact, discard: handleDiscard });
  });

  if (!versions || !routingRules) return <Skeleton className="h-96 rounded-md" />;

  const myChanges = changes.filter((c) => c.sectionId === SECTION_ID);
  const active = controls.filter((c) => c.status === 'active');
  const retired = controls.filter((c) => c.status === 'retired');

  function confirmEdit(id: string, patch: { name: string; weight: number; effectiveness: number; evidence: InventoryEvidence }) {
    const prev = controls.find((c) => c.id === id);
    if (!prev) return;
    setControls((cs) => cs.map((c) => (c.id === id ? { ...c, ...patch } : c)));
    log(`Edited "${prev.name}"`, () => setControls((cs) => cs.map((c) => (c.id === id ? prev : c))));
    setEditingId(null);
  }

  function addControl(category: AmlCategory, input: { name: string; weight: number; effectiveness: number }) {
    const id = newId('ctl');
    const item: ControlInventoryItem = {
      id,
      name: input.name,
      category,
      weight: input.weight,
      effectiveness: input.effectiveness,
      evidence: BLANK_EVIDENCE,
      source: 'Analyst-added',
      status: 'active',
    };
    setControls((cs) => [...cs, item]);
    log(`Added "${input.name}" to ${category}`, () => setControls((cs) => cs.filter((c) => c.id !== id)));
    setAddOpenFor(null);
  }

  async function requestRemove(item: ControlInventoryItem) {
    const inUse = await isControlInUse(item.id);
    setRemoveTarget({ item, inUse });
  }

  function confirmRemove() {
    if (!removeTarget) return;
    const { item, inUse } = removeTarget;
    if (inUse) {
      setControls((cs) => cs.map((c) => (c.id === item.id ? { ...c, status: 'retired' as const } : c)));
      log(`Retired "${item.name}"`, () => setControls((cs) => cs.map((c) => (c.id === item.id ? { ...c, status: 'active' as const } : c))));
    } else {
      setControls((cs) => cs.filter((c) => c.id !== item.id));
      log(`Deleted "${item.name}"`, () => setControls((cs) => [...cs, item]));
    }
    setRemoveTarget(null);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-snug">
        <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
        Controls are the mechanisms already in place that reduce risk. Each carries a control CE (0 to 10) and a weight within its AML category. The weighted mean produces
        the category CE, which determines how much inherent risk survives as residual risk. Residual risk sets the band, and the band decides routing.
      </div>

      <div className="flex items-start gap-2.5 bg-verdant-1 rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-snug">
        <Icon name="sparkles" size={14} className="text-teal shrink-0 mt-0.5" />
        Staleness will be surfaced at module level for immediate action in a later phase. This build shows the state (current, nearing, stale) and an action affordance without acting on
        it - a stale control still carries its full CE into the assessment exactly as today.
      </div>

      <div className="flex justify-end">
        <Button variant="secondary" icon={<Icon name="upload" size={14} />} onClick={() => setImportOpen(true)}>
          Import from file
        </Button>
      </div>

      {ALL_AML_CATEGORIES.map((category) => {
        const items = active.filter((c) => c.category === category);
        const total = categoryWeightTotal(controls, category);
        const balanced = Math.abs(total - 1) <= 0.01;
        return (
          <Card key={category} material="flat" className="p-5 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <span className="text-sm font-bold text-text-primary">{category}</span>
              <div className="flex items-center gap-2">
                <span className="text-[11.5px] text-text-faint">Weight total</span>
                <span className={`font-mono text-[13px] font-semibold ${balanced ? 'text-teal' : 'text-danger'}`}>{total.toFixed(2)} / 1.00</span>
                {balanced ? <Badge tone="success">Balanced</Badge> : <Badge tone="danger">Unbalanced</Badge>}
              </div>
            </div>
            <div className="grid grid-cols-[1.6fr_70px_60px_1.6fr_110px_110px_auto] gap-3 px-2 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">
              <span>Control</span>
              <span>Weight</span>
              <span>CE</span>
              <span>Evidence</span>
              <span>State</span>
              <span>Action</span>
              <span className="text-right">Manage</span>
            </div>
            {items.length === 0 && addOpenFor !== category && <p className="text-[12px] text-text-faint px-2">No active controls.</p>}
            {items.map((c) => (
              <ControlRow
                key={c.id}
                c={c}
                editing={editingId === c.id}
                recencyMonths={routingRules.evidenceRecencyMonths}
                nearingMonths={routingRules.nearingStalenessMonths}
                onStartEdit={() => setEditingId(c.id)}
                onCancelEdit={() => setEditingId(null)}
                onConfirmEdit={(patch) => confirmEdit(c.id, patch)}
                onRemove={() => requestRemove(c)}
              />
            ))}
            {addOpenFor === category ? (
              <AddControlRow category={category} onAdd={(input) => addControl(category, input)} onCancel={() => setAddOpenFor(null)} />
            ) : (
              <Button size="sm" variant="ghost" className="self-start" icon={<Icon name="plus" size={13} />} onClick={() => setAddOpenFor(category)}>
                Add control
              </Button>
            )}
          </Card>
        );
      })}

      {retired.length > 0 && (
        <Card material="flat" className="p-5 flex flex-col gap-2">
          <span className="text-sm font-bold text-text-primary">Retired</span>
          <p className="text-[12px] text-text-muted -mt-1">Unavailable to new assessments; still shown on cases that used them.</p>
          <div className="flex flex-col divide-y divide-border-hairline">
            {retired.map((c) => (
              <div key={c.id} className="flex items-center gap-3 py-2 text-[12.5px]">
                <span className="text-text-primary flex-1">{c.name}</span>
                <span className="text-text-faint">{c.category}</span>
                <Badge tone="default">Retired</Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card material="flat" className="p-5 flex flex-col gap-2">
        <span className="text-sm font-bold text-text-primary">Version history</span>
        <div className="flex flex-col divide-y divide-border-hairline">
          {versions
            .slice()
            .reverse()
            .map((v) => (
              <div key={v.version} className="flex items-center gap-3 py-2.5 text-[13px]">
                <span className="font-mono text-text-faint w-10">v{v.version}</span>
                <span className="text-text-secondary flex-1">{v.note}</span>
                <span className="text-text-faint text-[12px]">{v.changedBy}</span>
                <span className="text-text-faint text-[12px]">{formatDate(v.publishedAt)}</span>
              </div>
            ))}
        </div>
      </Card>

      <Modal open={!!removeTarget} onClose={() => setRemoveTarget(null)} title={removeTarget?.inUse ? 'Retire control' : 'Delete control'} width={460}>
        {removeTarget && (
          <div className="flex flex-col gap-4">
            {removeTarget.inUse ? (
              <p className="text-[13px] text-text-secondary">
                <strong>"{removeTarget.item.name}"</strong> is referenced by at least one assessed case. It will be <strong>retired</strong>: unavailable to new assessments, but still
                rendering on the cases that already used it.
              </p>
            ) : (
              <p className="text-[13px] text-text-secondary">
                <strong>"{removeTarget.item.name}"</strong> has never been used in an assessed case. It can be <strong>deleted</strong> outright.
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

      <Modal open={impactOpen} onClose={() => setImpactOpen(false)} title="Publish control inventory" width={560}>
        <div className="flex flex-col gap-4">
          {weightGaps.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              <div className="flex items-start gap-2.5 bg-danger-bg rounded-md px-3.5 py-3">
                <Icon name="warning" size={14} className="text-danger shrink-0 mt-0.5" />
                <p className="text-[13px] text-danger leading-relaxed">Cannot publish - the following categories' active-control weights do not sum to 1.0:</p>
              </div>
              {weightGaps.map((g) => (
                <div key={g.category} className="flex items-center justify-between px-3 py-2 rounded-md bg-surface-tertiary text-[12.5px]">
                  <span className="text-text-secondary">{g.category}</span>
                  <span className="font-mono font-semibold text-danger">
                    {g.total.toFixed(2)} ({g.gap > 0 ? `short by ${g.gap.toFixed(2)}` : `over by ${Math.abs(g.gap).toFixed(2)}`})
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <>
              <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3 py-2.5">
                <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
                <p className="text-[12px] text-[#133338]">
                  {myChanges.length} pending change{myChanges.length === 1 ? '' : 's'} - every category's active-control weights sum to 1.0.
                </p>
              </div>
              <Textarea rows={2} placeholder="Version note (what changed and why)..." value={note} onChange={(e) => setNote(e.target.value)} />
            </>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setImpactOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={handlePublish} loading={publishing} disabled={weightGaps.length > 0}>
              Confirm and publish new version
            </Button>
          </div>
        </div>
      </Modal>

      <ImportWizard
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onCommitted={() => {
          toast('Import committed as a new control inventory version.', 'success');
          reload();
        }}
      />
    </div>
  );
}
