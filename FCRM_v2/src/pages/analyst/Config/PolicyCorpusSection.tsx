import { useEffect, useState } from 'react';
import type { AmlCategory, PolicyClauseCorpusEntry, PolicySource } from '@/types/analyst';
import { ALL_AML_CATEGORIES } from '@/types/analyst';
import {
  commitDocumentIngestion,
  commitPolicyCorpusChange,
  getAllClauseCitations,
  getPolicyCorpus,
  getPolicyCorpusVersions,
  getPolicySources,
  getSourceCitations,
  previewChunkDocument,
  previewReissue,
  type ProposedClause,
  type ReissueDiff,
} from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge, Tag } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Select, Textarea, Checkbox } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/utils/format';
import { useWorkingSet } from './WorkingSetContext';

const SECTION_ID = 'policy';
const SECTION_LABEL = 'Policy Corpus';

function newId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// -----------------------------------------------------------------------
// Clause row
// -----------------------------------------------------------------------
function ClauseRow({
  clause,
  editing,
  citations,
  onStartEdit,
  onCancelEdit,
  onConfirmEdit,
  onRemove,
  allTriggerTags,
}: {
  clause: PolicyClauseCorpusEntry;
  editing: boolean;
  citations: { caseId: string; title: string }[];
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onConfirmEdit: (patch: { clauseText: string; amlCategories: AmlCategory[]; triggerTags: string[] }) => void;
  onRemove: () => void;
  allTriggerTags: string[];
}) {
  const [text, setText] = useState(clause.clauseText);
  const [cats, setCats] = useState<AmlCategory[]>(clause.amlCategories);
  const [trig, setTrig] = useState<string[]>(clause.triggerTags);
  const locked = clause.origin === 'external';

  useEffect(() => {
    if (editing) {
      setText(clause.clauseText);
      setCats(clause.amlCategories);
      setTrig(clause.triggerTags);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  function toggleCat(c: AmlCategory) {
    setCats((cs) => (cs.includes(c) ? cs.filter((x) => x !== c) : [...cs, c]));
  }
  function toggleTrig(t: string) {
    setTrig((ts) => (ts.includes(t) ? ts.filter((x) => x !== t) : [...ts, t]));
  }

  if (editing) {
    return (
      <Card material="flat" className="p-4 flex flex-col gap-3 border border-carbon-moss">
        {locked ? (
          <div className="flex items-start gap-2 bg-surface-tertiary rounded-md px-3 py-2.5">
            <Icon name="lock" size={13} className="text-text-faint shrink-0 mt-0.5" />
            <p className="text-[13px] text-text-secondary italic">"{clause.clauseText}"</p>
          </div>
        ) : (
          <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} className="text-[13px]" />
        )}
        <div className="flex flex-col gap-1.5">
          <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">AML categories</span>
          <div className="flex flex-wrap gap-2">
            {ALL_AML_CATEGORIES.map((c) => (
              <Checkbox key={c} checked={cats.includes(c)} onChange={() => toggleCat(c)} label={<span className="text-[12px]">{c}</span>} />
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Trigger tags</span>
          <div className="flex flex-wrap gap-2">
            {allTriggerTags.map((t) => (
              <Checkbox key={t} checked={trig.includes(t)} onChange={() => toggleTrig(t)} label={<span className="text-[12px]">{t}</span>} />
            ))}
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-1 border-t border-border-hairline">
          <Button size="sm" variant="ghost" onClick={onCancelEdit}>
            Cancel
          </Button>
          <Button size="sm" variant="primary" onClick={() => onConfirmEdit({ clauseText: locked ? clause.clauseText : text, amlCategories: cats, triggerTags: trig })}>
            Confirm
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <Card material="flat" className={`p-4 flex flex-col gap-2 ${clause.retired ? 'opacity-60' : ''}`}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[13px] text-text-primary leading-relaxed italic flex-1">
          {locked && <Icon name="lock" size={12} className="inline mr-1.5 text-text-faint" />}"{clause.clauseText}"
        </p>
        {clause.retired && <Badge tone="default">Retired</Badge>}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11.5px] text-text-faint">{clause.section}</span>
        <Badge tone={clause.origin === 'internal' ? 'default' : 'info'}>{clause.origin}</Badge>
        {clause.amlCategories.map((c) => (
          <Tag key={c} className="text-[11px] px-2 py-0.5">
            {c}
          </Tag>
        ))}
        {clause.triggerTags.map((t) => (
          <Tag key={t} className="text-[11px] px-2 py-0.5 bg-[#EAF3F3]">
            {t}
          </Tag>
        ))}
      </div>
      {clause.retired ? (
        <p className="text-[11.5px] text-text-faint">
          Retired{clause.retiredAt ? ` ${formatDate(clause.retiredAt)}` : ''}
          {clause.supersededBy && ' - superseded by the replacement clause below'}
        </p>
      ) : (
        <div className="flex items-center justify-between">
          <span className="text-[11.5px] text-text-faint">
            {citations.length === 0 ? 'Not yet cited' : `Cited on ${citations.length} case${citations.length === 1 ? '' : 's'}`}
            {citations.length > 0 && (
              <span className="ml-1.5">
                {citations.map((c, i) => (
                  <span key={c.caseId}>
                    {i > 0 && ', '}
                    <a href={`/analyst/case/${c.caseId}`} className="text-carbon-moss hover:text-carbon-moss-hover underline">
                      {c.caseId}
                    </a>
                  </span>
                ))}
              </span>
            )}
          </span>
          <div className="flex items-center gap-1 border border-border-strong rounded-md p-1">
            <Button size="sm" variant="ghost" onClick={onStartEdit}>
              Edit
            </Button>
            <Button size="sm" variant="ghost" onClick={onRemove}>
              Retire
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function AddClauseForm({ sources, defaultSourceId, allTriggerTags, onAdd, onCancel }: { sources: PolicySource[]; defaultSourceId: string; allTriggerTags: string[]; onAdd: (input: { sourceId: string; section: string; clauseText: string; amlCategories: AmlCategory[]; triggerTags: string[] }) => void; onCancel: () => void }) {
  const [sourceId, setSourceId] = useState(defaultSourceId);
  const [section, setSection] = useState('');
  const [text, setText] = useState('');
  const [cats, setCats] = useState<AmlCategory[]>([]);
  const [trig, setTrig] = useState<string[]>([]);

  return (
    <Card material="flat" className="p-4 flex flex-col gap-3 border border-carbon-moss">
      <span className="text-[12.5px] font-semibold text-text-primary">Add clause</span>
      <Select value={sourceId} onChange={(e) => setSourceId(e.target.value)} className="text-[12.5px]">
        {sources.filter((s) => s.status === 'active').map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </Select>
      <Input value={section} onChange={(e) => setSection(e.target.value)} placeholder="Section (e.g. §2.4)" className="text-[12.5px]" />
      <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Clause text" className="text-[13px]" />
      <div className="flex flex-col gap-1.5">
        <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">AML categories</span>
        <div className="flex flex-wrap gap-2">
          {ALL_AML_CATEGORIES.map((c) => (
            <Checkbox key={c} checked={cats.includes(c)} onChange={() => setCats((cs) => (cs.includes(c) ? cs.filter((x) => x !== c) : [...cs, c]))} label={<span className="text-[12px]">{c}</span>} />
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Trigger tags</span>
        <div className="flex flex-wrap gap-2">
          {allTriggerTags.map((t) => (
            <Checkbox key={t} checked={trig.includes(t)} onChange={() => setTrig((ts) => (ts.includes(t) ? ts.filter((x) => x !== t) : [...ts, t]))} label={<span className="text-[12px]">{t}</span>} />
          ))}
        </div>
      </div>
      <div className="flex justify-end gap-2 pt-1 border-t border-border-hairline">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" disabled={!text.trim() || !section.trim() || cats.length === 0} onClick={() => onAdd({ sourceId, section: section.trim(), clauseText: text.trim(), amlCategories: cats, triggerTags: trig })}>
          Add
        </Button>
      </div>
    </Card>
  );
}

// -----------------------------------------------------------------------
// Upload wizard - new source or reissue, both go through chunk -> batch review -> (diff) -> commit
// -----------------------------------------------------------------------
function UploadWizard({ sources, onClose, onCommitted }: { sources: PolicySource[]; onClose: () => void; onCommitted: () => void }) {
  const toast = useToast();
  const [mode, setMode] = useState<'new-source' | 'reissue'>('new-source');
  const [sourceId, setSourceId] = useState('');
  const [newName, setNewName] = useState('');
  const [newOrigin, setNewOrigin] = useState<'internal' | 'external'>('internal');
  const [newVersion, setNewVersion] = useState('v1');
  const [newEffectiveDate, setNewEffectiveDate] = useState('');
  const [fileName, setFileName] = useState('');
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [proposed, setProposed] = useState<ProposedClause[]>([]);
  const [discarded, setDiscarded] = useState<Set<string>>(new Set());
  const [diff, setDiff] = useState<ReissueDiff | null>(null);
  const [committing, setCommitting] = useState(false);

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = async () => {
      const chunks = await previewChunkDocument(String(reader.result ?? ''));
      setProposed(chunks);
      setDiscarded(new Set());
      if (mode === 'reissue' && sourceId) {
        const d = await previewReissue(sourceId, chunks);
        setDiff(d);
      }
      setStep(2);
    };
    reader.readAsText(file);
  }

  function updateProposed(tempId: string, patch: Partial<ProposedClause>) {
    setProposed((ps) => ps.map((p) => (p.tempId === tempId ? { ...p, ...patch } : p)));
  }
  function toggleDiscard(tempId: string) {
    setDiscarded((s) => {
      const next = new Set(s);
      if (next.has(tempId)) next.delete(tempId);
      else next.add(tempId);
      return next;
    });
  }

  async function goToReview() {
    setStep(3);
  }

  async function confirmCommit() {
    const kept = proposed.filter((p) => !discarded.has(p.tempId));
    let added: ProposedClause[];
    let changed: { proposed: ProposedClause; existingId: string }[] = [];
    if (mode === 'reissue' && diff) {
      const changedTempIds = new Set(diff.changed.map((c) => c.proposed.tempId));
      added = kept.filter((p) => !changedTempIds.has(p.tempId) && diff.changed.every((c) => c.proposed.tempId !== p.tempId) && !diff.unchanged.some((u) => u.section === p.section));
      changed = diff.changed.filter((c) => kept.some((p) => p.tempId === c.proposed.tempId)).map((c) => ({ proposed: kept.find((p) => p.tempId === c.proposed.tempId)!, existingId: c.existing.id }));
    } else {
      added = kept;
    }
    setCommitting(true);
    await commitDocumentIngestion({
      mode,
      sourceId: mode === 'reissue' ? sourceId : undefined,
      newSource: mode === 'new-source' ? { name: newName, origin: newOrigin, version: newVersion, effectiveDate: newEffectiveDate || new Date().toISOString().slice(0, 10) } : undefined,
      added,
      changed,
      fileName,
    });
    setCommitting(false);
    toast('Committed as a new Policy Corpus version.', 'success');
    onCommitted();
    onClose();
  }

  const canUpload = mode === 'new-source' ? newName.trim().length > 0 : sourceId.length > 0;

  return (
    <Modal open onClose={onClose} title="Upload a policy document" width={680}>
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3.5 py-3">
          <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
          <p className="text-[12px] text-[#133338] leading-relaxed">
            This build parses plain text (.txt) directly - export a PDF or Word document to plain text first. Chunking splits on blank lines and tag proposal is keyword-based, not a real
            model call, but nothing commits until you confirm the batch below.
          </p>
        </div>

        {step === 1 && (
          <div className="flex flex-col gap-3">
            <div className="flex gap-2">
              <Button size="sm" variant={mode === 'new-source' ? 'primary' : 'ghost'} onClick={() => setMode('new-source')}>
                New source
              </Button>
              <Button size="sm" variant={mode === 'reissue' ? 'primary' : 'ghost'} onClick={() => setMode('reissue')}>
                Reissue an existing source
              </Button>
            </div>
            {mode === 'new-source' ? (
              <div className="grid grid-cols-2 gap-2.5">
                <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Source name" className="col-span-2" />
                <Select value={newOrigin} onChange={(e) => setNewOrigin(e.target.value as 'internal' | 'external')}>
                  <option value="internal">Internal</option>
                  <option value="external">External</option>
                </Select>
                <Input value={newVersion} onChange={(e) => setNewVersion(e.target.value)} placeholder="Version (e.g. v1)" />
                <Input type="date" value={newEffectiveDate} onChange={(e) => setNewEffectiveDate(e.target.value)} className="col-span-2" />
              </div>
            ) : (
              <Select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                <option value="">Select a source to reissue...</option>
                {sources.filter((s) => s.status === 'active').map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.version})
                  </option>
                ))}
              </Select>
            )}
            <input type="file" accept=".txt,.csv,.pdf,.docx" disabled={!canUpload} onChange={handleFile} className="text-[13px]" />
          </div>
        )}

        {step === 2 && (
          <div className="flex flex-col gap-3">
            <p className="text-[12.5px] text-text-muted">
              {fileName} chunked into {proposed.length} candidate clause{proposed.length === 1 ? '' : 's'}. Review, edit tags, or discard before anything commits.
            </p>
            <div className="flex flex-col gap-2 max-h-[420px] overflow-y-auto">
              {proposed.map((p) => (
                <div key={p.tempId} className={`border rounded-md p-3 flex flex-col gap-2 ${discarded.has(p.tempId) ? 'opacity-40 border-border-hairline' : 'border-border-strong'}`}>
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-[11.5px] font-mono font-semibold text-text-faint">{p.section}</span>
                    <Checkbox checked={!discarded.has(p.tempId)} onChange={() => toggleDiscard(p.tempId)} label={<span className="text-[11px]">Keep</span>} />
                  </div>
                  <Textarea rows={2} value={p.clauseText} onChange={(e) => updateProposed(p.tempId, { clauseText: e.target.value })} className="text-[12.5px]" />
                  <div className="flex flex-wrap gap-1.5">
                    {ALL_AML_CATEGORIES.map((c) => (
                      <Checkbox
                        key={c}
                        checked={p.amlCategories.includes(c)}
                        onChange={() => updateProposed(p.tempId, { amlCategories: p.amlCategories.includes(c) ? p.amlCategories.filter((x) => x !== c) : [...p.amlCategories, c] })}
                        label={<span className="text-[11px]">{c}</span>}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
              <Button variant="ghost" onClick={() => setStep(1)}>
                Back
              </Button>
              <Button variant="primary" onClick={goToReview}>
                {mode === 'reissue' ? 'Preview diff' : 'Review'}
              </Button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="flex flex-col gap-4">
            {mode === 'reissue' && diff ? (
              <>
                <div className="flex flex-col gap-1.5">
                  <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Added ({diff.added.filter((p) => !discarded.has(p.tempId)).length})</span>
                  {diff.added.filter((p) => !discarded.has(p.tempId)).map((p) => (
                    <div key={p.tempId} className="px-3 py-1.5 rounded-md bg-surface-tertiary text-[12.5px] text-text-secondary">
                      {p.section} - {p.clauseText.slice(0, 90)}...
                    </div>
                  ))}
                </div>
                <div className="flex flex-col gap-1.5">
                  <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Changed ({diff.changed.filter((c) => !discarded.has(c.proposed.tempId)).length})</span>
                  {diff.changed.filter((c) => !discarded.has(c.proposed.tempId)).map((c) => (
                    <div key={c.proposed.tempId} className="px-3 py-1.5 rounded-md bg-surface-tertiary text-[12.5px] text-text-secondary flex flex-col gap-1">
                      <span className="font-medium text-text-primary">{c.existing.section}</span>
                      <span className="text-[11.5px] text-text-faint">Old: "{c.existing.clauseText.slice(0, 80)}..."</span>
                      <span className="text-[11.5px] text-text-faint">New: "{c.proposed.clauseText.slice(0, 80)}..."</span>
                      <span className="text-[11px] text-teal">Old clause retires with a supersession link; new clause created.</span>
                    </div>
                  ))}
                </div>
                <div className="flex flex-col gap-1.5">
                  <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Unchanged ({diff.unchanged.length})</span>
                  <p className="text-[12px] text-text-faint">No action - existing wording matches.</p>
                </div>
              </>
            ) : (
              <div className="flex flex-col gap-1.5">
                <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Will be added ({proposed.filter((p) => !discarded.has(p.tempId)).length})</span>
                {proposed.filter((p) => !discarded.has(p.tempId)).map((p) => (
                  <div key={p.tempId} className="px-3 py-1.5 rounded-md bg-surface-tertiary text-[12.5px] text-text-secondary">
                    {p.section} - {p.clauseText.slice(0, 90)}...
                  </div>
                ))}
              </div>
            )}
            <div className="flex justify-end gap-2 pt-2 border-t border-border-hairline">
              <Button variant="ghost" onClick={() => setStep(2)}>
                Back
              </Button>
              <Button variant="primary" onClick={confirmCommit} loading={committing}>
                Confirm and publish new version
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
export function PolicyCorpusSection() {
  const { changes, addChange, clearSection, registerActions } = useWorkingSet();
  const toast = useToast();

  const [sources, setSources] = useState<PolicySource[]>([]);
  const [clauses, setClauses] = useState<PolicyClauseCorpusEntry[]>([]);
  const [initial, setInitial] = useState<{ sources: PolicySource[]; clauses: PolicyClauseCorpusEntry[] } | null>(null);
  const [versions, setVersions] = useState<{ version: number; publishedAt: string; note: string; changedBy: string }[] | null>(null);
  const [citations, setCitations] = useState<Record<string, { caseId: string; title: string }[]>>({});

  const [showRetired, setShowRetired] = useState(false);
  const [query, setQuery] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [filterTrigger, setFilterTrigger] = useState('');
  const [filterOrigin, setFilterOrigin] = useState('');
  const [filterSource, setFilterSource] = useState('');
  const [expandedSources, setExpandedSources] = useState<Set<string>>(new Set());
  const [sourcesOpen, setSourcesOpen] = useState(true);

  const [editingClauseId, setEditingClauseId] = useState<string | null>(null);
  const [addOpenForSource, setAddOpenForSource] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<{ kind: 'clause' | 'source'; id: string; label: string; cited: boolean } | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const [impactOpen, setImpactOpen] = useState(false);
  const [note, setNote] = useState('');
  const [publishing, setPublishing] = useState(false);

  async function reload() {
    const [c, s, vers, cit] = await Promise.all([getPolicyCorpus(), getPolicySources(), getPolicyCorpusVersions(), getAllClauseCitations()]);
    setClauses(c);
    setSources(s);
    setInitial({ sources: s, clauses: c });
    setVersions(vers);
    setCitations(cit);
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function log(label: string, revert: () => void) {
    addChange({ label, sectionId: SECTION_ID, sectionLabel: SECTION_LABEL, revert });
  }

  function handleReviewImpact() {
    setImpactOpen(true);
  }

  async function handlePublish() {
    setPublishing(true);
    await commitPolicyCorpusChange({ sources, clauses }, note.trim() || 'Policy corpus update.');
    setPublishing(false);
    setImpactOpen(false);
    setNote('');
    clearSection(SECTION_ID);
    toast('New Policy Corpus version published.', 'success');
    reload();
  }

  function handleDiscard() {
    if (!initial) return;
    setSources(initial.sources);
    setClauses(initial.clauses);
    clearSection(SECTION_ID);
  }

  useEffect(() => {
    registerActions(SECTION_ID, { publish: handleReviewImpact, discard: handleDiscard });
  });

  if (!versions) return <Skeleton className="h-96 rounded-md" />;

  const myChanges = changes.filter((c) => c.sectionId === SECTION_ID);
  const allTriggerTags = Array.from(new Set(clauses.flatMap((c) => c.triggerTags))).sort();
  const activeClauses = clauses.filter((c) => !c.retired);
  const retiredClauses = clauses.filter((c) => c.retired);

  function toggleSourceExpanded(id: string) {
    setExpandedSources((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function confirmEdit(id: string, patch: { clauseText: string; amlCategories: AmlCategory[]; triggerTags: string[] }) {
    const prev = clauses.find((c) => c.id === id);
    if (!prev) return;
    setClauses((cs) => cs.map((c) => (c.id === id ? { ...c, ...patch } : c)));
    log(`Edited clause ${prev.section}`, () => setClauses((cs) => cs.map((c) => (c.id === id ? prev : c))));
    setEditingClauseId(null);
  }

  function addClause(input: { sourceId: string; section: string; clauseText: string; amlCategories: AmlCategory[]; triggerTags: string[] }) {
    const source = sources.find((s) => s.id === input.sourceId)!;
    const id = newId('pc');
    const item: PolicyClauseCorpusEntry = { id, sourceId: input.sourceId, clauseText: input.clauseText, sourceDoc: source.name, section: input.section, amlCategories: input.amlCategories, triggerTags: input.triggerTags, origin: source.origin, retired: false };
    setClauses((cs) => [...cs, item]);
    log(`Added clause ${input.section} to ${source.name}`, () => setClauses((cs) => cs.filter((c) => c.id !== id)));
    setAddOpenForSource(null);
  }

  async function requestRemoveClause(clause: PolicyClauseCorpusEntry) {
    const cited = (citations[clause.id]?.length ?? 0) > 0;
    setRemoveTarget({ kind: 'clause', id: clause.id, label: `${clause.sourceDoc} ${clause.section}`, cited });
  }
  async function requestRemoveSource(source: PolicySource) {
    const list = await getSourceCitations(source.id);
    setRemoveTarget({ kind: 'source', id: source.id, label: source.name, cited: list.length > 0 });
  }

  function confirmRemove() {
    if (!removeTarget) return;
    if (removeTarget.kind === 'clause') {
      const item = clauses.find((c) => c.id === removeTarget.id)!;
      if (removeTarget.cited) {
        setClauses((cs) => cs.map((c) => (c.id === item.id ? { ...c, retired: true, retiredAt: new Date().toISOString().slice(0, 10) } : c)));
        log(`Retired clause ${item.section}`, () => setClauses((cs) => cs.map((c) => (c.id === item.id ? { ...c, retired: false, retiredAt: undefined } : c))));
      } else {
        setClauses((cs) => cs.filter((c) => c.id !== item.id));
        log(`Deleted clause ${item.section}`, () => setClauses((cs) => [...cs, item]));
      }
    } else {
      const source = sources.find((s) => s.id === removeTarget.id)!;
      const affectedClauseIds = clauses.filter((c) => c.sourceId === source.id).map((c) => c.id);
      if (removeTarget.cited) {
        setSources((ss) => ss.map((s) => (s.id === source.id ? { ...s, status: 'retired' as const } : s)));
        setClauses((cs) => cs.map((c) => (affectedClauseIds.includes(c.id) ? { ...c, retired: true, retiredAt: new Date().toISOString().slice(0, 10) } : c)));
        log(`Retired source "${source.name}" and its clauses`, () => {
          setSources((ss) => ss.map((s) => (s.id === source.id ? { ...s, status: 'active' as const } : s)));
          setClauses((cs) => cs.map((c) => (affectedClauseIds.includes(c.id) ? { ...c, retired: false, retiredAt: undefined } : c)));
        });
      } else {
        setSources((ss) => ss.filter((s) => s.id !== source.id));
        setClauses((cs) => cs.filter((c) => c.sourceId !== source.id));
        const removedClauses = clauses.filter((c) => c.sourceId === source.id);
        log(`Deleted source "${source.name}" and its clauses`, () => {
          setSources((ss) => [...ss, source]);
          setClauses((cs) => [...cs, ...removedClauses]);
        });
      }
    }
    setRemoveTarget(null);
  }

  const filteredActive = activeClauses.filter((c) => {
    if (filterCategory && !c.amlCategories.includes(filterCategory as AmlCategory)) return false;
    if (filterTrigger && !c.triggerTags.includes(filterTrigger)) return false;
    if (filterOrigin && c.origin !== filterOrigin) return false;
    if (filterSource && c.sourceId !== filterSource) return false;
    const q = query.trim().toLowerCase();
    if (q && !c.clauseText.toLowerCase().includes(q) && !c.sourceDoc.toLowerCase().includes(q)) return false;
    return true;
  });
  const filteredRetired = retiredClauses.filter((c) => {
    if (filterSource && c.sourceId !== filterSource) return false;
    return true;
  });

  const bySource = new Map<string, PolicyClauseCorpusEntry[]>();
  for (const c of filteredActive) {
    if (!bySource.has(c.sourceId)) bySource.set(c.sourceId, []);
    bySource.get(c.sourceId)!.push(c);
  }
  const retiredBySource = new Map<string, PolicyClauseCorpusEntry[]>();
  for (const c of filteredRetired) {
    if (!retiredBySource.has(c.sourceId)) retiredBySource.set(c.sourceId, []);
    retiredBySource.get(c.sourceId)!.push(c);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <Button variant="secondary" icon={<Icon name="upload" size={14} />} onClick={() => setUploadOpen(true)}>
          Upload policy document
        </Button>
      </div>

      <Card material="flat" className="overflow-hidden">
        <button onClick={() => setSourcesOpen((o) => !o)} className="w-full flex items-center justify-between px-5 py-4 cursor-pointer">
          <span className="text-sm font-bold text-text-primary">Sources ({sources.filter((s) => s.status === 'active').length} active)</span>
          <Icon name={sourcesOpen ? 'chevron-down' : 'chevron-right'} size={15} className="text-text-faint" />
        </button>
        {sourcesOpen && (
          <div className="px-5 pb-5 flex flex-col divide-y divide-border-hairline">
            {sources
              .filter((s) => showRetired || s.status === 'active')
              .map((s) => {
                const clauseCount = clauses.filter((c) => c.sourceId === s.id && !c.retired).length;
                const citedCount = clauses.filter((c) => c.sourceId === s.id).reduce((sum, c) => sum + (citations[c.id]?.length ?? 0), 0);
                return (
                  <div key={s.id} className={`flex items-center gap-3 py-3 flex-wrap ${s.status === 'retired' ? 'opacity-60' : ''}`}>
                    <div className="flex flex-col gap-0.5 flex-1 min-w-[220px]">
                      <span className="text-[13.5px] font-semibold text-text-primary">{s.name}</span>
                      <span className="text-[11.5px] text-text-faint">
                        {s.version} - effective {formatDate(s.effectiveDate)} - updated {formatDate(s.lastUpdated)}
                      </span>
                    </div>
                    <Badge tone={s.origin === 'internal' ? 'default' : 'info'}>{s.origin}</Badge>
                    <span className="text-[12px] text-text-secondary w-24">{clauseCount} clauses</span>
                    <span className="text-[12px] text-text-secondary w-28">{citedCount} citation{citedCount === 1 ? '' : 's'}</span>
                    {s.status === 'retired' ? (
                      <Badge tone="default">Retired</Badge>
                    ) : (
                      <div className="flex items-center gap-1 border border-border-strong rounded-md p-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setAddOpenForSource(s.id);
                            setExpandedSources((prev) => (prev.has(s.id) ? prev : new Set(prev).add(s.id)));
                          }}
                        >
                          Add clause
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => requestRemoveSource(s)}>
                          Retire
                        </Button>
                      </div>
                    )}
                  </div>
                );
              })}
          </div>
        )}
      </Card>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[220px]">
          <Icon name="search" size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-faint" />
          <Input placeholder="Search clause text or source..." value={query} onChange={(e) => setQuery(e.target.value)} className="pl-9" />
        </div>
        <Select value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)} className="w-[190px]">
          <option value="">AML category - any</option>
          {ALL_AML_CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        <Select value={filterTrigger} onChange={(e) => setFilterTrigger(e.target.value)} className="w-[190px]">
          <option value="">Trigger type - any</option>
          {allTriggerTags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
        <Select value={filterOrigin} onChange={(e) => setFilterOrigin(e.target.value)} className="w-[150px]">
          <option value="">Internal or external - any</option>
          <option value="internal">Internal</option>
          <option value="external">External</option>
        </Select>
        <Select value={filterSource} onChange={(e) => setFilterSource(e.target.value)} className="w-[220px]">
          <option value="">Source document - any</option>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
      </div>

      <div className="flex items-center justify-between">
        <span className="text-[12.5px] text-text-muted">
          {activeClauses.length} active clause{activeClauses.length === 1 ? '' : 's'} &middot; {retiredClauses.length} retired
        </span>
        <label className="flex items-center gap-2 text-[12.5px] text-text-secondary cursor-pointer">
          <Checkbox checked={showRetired} onChange={setShowRetired} />
          Show retired
        </label>
      </div>

      {Array.from(new Set([...bySource.keys(), ...(addOpenForSource ? [addOpenForSource] : [])])).map((sourceId) => {
        const items = bySource.get(sourceId) ?? [];
        const source = sources.find((s) => s.id === sourceId);
        const expanded = expandedSources.has(sourceId);
        return (
          <Card key={sourceId} material="flat" className="overflow-hidden">
            <button onClick={() => toggleSourceExpanded(sourceId)} className="w-full flex items-center justify-between px-5 py-3.5 cursor-pointer">
              <span className="text-[13.5px] font-semibold text-text-primary">{source?.name ?? sourceId}</span>
              <div className="flex items-center gap-2">
                <span className="text-[12px] text-text-faint">{items.length} clauses</span>
                <Icon name={expanded ? 'chevron-down' : 'chevron-right'} size={14} className="text-text-faint" />
              </div>
            </button>
            {expanded && (
              <div className="px-5 pb-5 flex flex-col gap-2.5">
                {items.length === 0 && addOpenForSource !== sourceId && <p className="text-[12px] text-text-faint">No clauses match the current filters.</p>}
                {items.map((c) => (
                  <ClauseRow
                    key={c.id}
                    clause={c}
                    editing={editingClauseId === c.id}
                    citations={citations[c.id] ?? []}
                    onStartEdit={() => setEditingClauseId(c.id)}
                    onCancelEdit={() => setEditingClauseId(null)}
                    onConfirmEdit={(patch) => confirmEdit(c.id, patch)}
                    onRemove={() => requestRemoveClause(c)}
                    allTriggerTags={allTriggerTags}
                  />
                ))}
                {addOpenForSource === sourceId ? (
                  <AddClauseForm sources={sources} defaultSourceId={sourceId} allTriggerTags={allTriggerTags} onAdd={addClause} onCancel={() => setAddOpenForSource(null)} />
                ) : (
                  <Button size="sm" variant="ghost" className="self-start" icon={<Icon name="plus" size={13} />} onClick={() => setAddOpenForSource(sourceId)}>
                    Add clause
                  </Button>
                )}
              </div>
            )}
          </Card>
        );
      })}

      {showRetired &&
        Array.from(retiredBySource.entries()).map(([sourceId, items]) => {
          const source = sources.find((s) => s.id === sourceId);
          return (
            <Card key={`retired-${sourceId}`} material="flat" className="p-5 flex flex-col gap-2.5 opacity-70">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-text-faint">Retired - {source?.name ?? sourceId}</span>
              {items.map((c) => {
                const replacement = c.supersededBy ? clauses.find((x) => x.id === c.supersededBy) : undefined;
                return (
                  <div key={c.id} className="border border-border-hairline rounded-md p-3 flex flex-col gap-1.5 bg-surface-tertiary">
                    <p className="text-[12.5px] text-text-secondary italic">"{c.clauseText}"</p>
                    <div className="flex items-center gap-2 text-[11px] text-text-faint">
                      <span>{c.section}</span>
                      <Badge tone="default">Retired{c.retiredAt ? ` ${formatDate(c.retiredAt)}` : ''}</Badge>
                      {replacement && <span>Superseded by {replacement.section} - "{replacement.clauseText.slice(0, 60)}..."</span>}
                    </div>
                  </div>
                );
              })}
            </Card>
          );
        })}

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

      <Modal open={!!removeTarget} onClose={() => setRemoveTarget(null)} title={removeTarget?.cited ? 'Retire' : 'Delete'} width={460}>
        {removeTarget && (
          <div className="flex flex-col gap-4">
            {removeTarget.cited ? (
              <p className="text-[13px] text-text-secondary">
                <strong>"{removeTarget.label}"</strong> is cited on at least one assessed case. It will be <strong>retired</strong>: hidden from Policy Mapping search and from the default
                view here, but the cases that cited it keep their original text with a retired note.
              </p>
            ) : (
              <p className="text-[13px] text-text-secondary">
                <strong>"{removeTarget.label}"</strong> has never been cited on an assessed case. It can be <strong>deleted</strong> outright.
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setRemoveTarget(null)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={confirmRemove}>
                {removeTarget.cited ? 'Retire' : 'Delete'}
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={impactOpen} onClose={() => setImpactOpen(false)} title="Publish policy corpus" width={480}>
        <div className="flex flex-col gap-4">
          <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3 py-2.5">
            <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
            <p className="text-[12px] text-[#133338]">
              {myChanges.length} pending change{myChanges.length === 1 ? '' : 's'}.
            </p>
          </div>
          <Textarea rows={2} placeholder="Version note (what changed and why)..." value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setImpactOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={handlePublish} loading={publishing}>
              Confirm and publish new version
            </Button>
          </div>
        </div>
      </Modal>

      {uploadOpen && <UploadWizard sources={sources} onClose={() => setUploadOpen(false)} onCommitted={reload} />}
    </div>
  );
}
