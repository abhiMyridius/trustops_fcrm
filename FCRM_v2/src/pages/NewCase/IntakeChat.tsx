import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import type { CaseDetail, ClassificationProposal, Document, FileRef, Integration, TriggerCategory, ValidationResult } from '@/types';
import {
  getCase,
  updateCaseTitle,
  appendChatMessage,
  recordPathChoice,
  uploadFiles,
  extractFields,
  classifyCategories,
  confirmCategories,
  removeCategoryTag,
  addCategoryTag,
  removeAddedCategoryTag,
  getCloneSuggestions,
  reuseCloneFields,
  applyFieldUpdates,
  editFieldValue,
  getIntakeTurn,
  runValidation,
  reopenIntake,
  submitCase,
  setCompletion,
  getScreeningTurn,
  approveAutoClear,
  getActiveFieldSetVersion,
  getIntegrations,
  importIntegrationDocuments,
} from '@/services/api';
import {
  DEMO_CASE_ID,
  DEMO_CASE_TITLE,
  STAGED_FILES,
  CLASSIFICATION_PROPOSALS,
  CATEGORY_REMOVAL_SUGGESTED_REASON,
  ROUND_1_QUESTIONS,
  ROUND_2_QUESTION,
  CLONE_SUGGESTIONS,
  RESUME_FIELD_UPDATES,
  RESUME_UPLOAD_FILE,
  type IntakeQuestion,
} from '@/mocks/scripts/intakeScript';
import { ChecklistPanel } from './ChecklistPanel';
import { ChatBubble, ReplyChips, TypingIndicator } from './ChatBubble';
import { ExtractionProgress } from './ExtractionProgress';
import { ClassificationCard } from './ClassificationCard';
import { CategoryReasonModal } from './CategoryReasonModal';
import { CloneSuggestionCard } from './CloneSuggestionCard';
import { CloneReuseModal } from './CloneReuseModal';
import { UploadWidget } from './UploadWidget';
import { BrowseSourceModal } from './BrowseSourceModal';
import { ValidationModal } from './ValidationModal';
import { CaseViewModal } from './CaseViewModal';
import { IntakeQuestionCard } from './IntakeQuestionCard';
import { DocumentViewModal } from '@/pages/CaseDetail/DocumentViewModal';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input } from '@/components/ui/Form';
import { useToast } from '@/components/ui/Toast';
import { mergedRequiredFields } from '@/mocks/seed/requiredFields';
import { summarizeTitle } from '@/utils/format';

type Feed =
  | { kind: 'bubble'; id: string; role: 'ai' | 'po'; text: string; animate?: boolean; attachments?: { name: string; sizeKb: number }[] }
  | { kind: 'typing'; id: string }
  | { kind: 'chips'; id: string; chips: string[]; onSelect: (v: string) => void }
  | { kind: 'entry-choice'; id: string }
  | { kind: 'upload'; id: string; files: { name: string; sizeKb: number }[]; onUpload: () => void }
  | { kind: 'extraction-progress'; id: string; onDone: () => void }
  | { kind: 'classification'; id: string; proposals: ClassificationProposal[]; removed: TriggerCategory[]; added: TriggerCategory[]; confirmed: TriggerCategory[] | null }
  | { kind: 'question'; id: string; questions: IntakeQuestion[] }
  | { kind: 'clone'; id: string }
  | { kind: 'auto-clear-approve'; id: string };

let uid = 0;
const nextId = () => `f${++uid}`;

async function liveCompletionPct(caseId: string): Promise<number> {
  const c = await getCase(caseId);
  return c.requiredFieldCount > 0 ? Math.round((c.requiredFieldsFilled / c.requiredFieldCount) * 100) : 0;
}

export function IntakeChat() {
  const { id: caseId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const isDemo = caseId === DEMO_CASE_ID;

  const [caseData, setCaseData] = useState<CaseDetail | null>(null);
  const [feed, setFeed] = useState<Feed[]>([]);
  const [input, setInput] = useState('');
  const [stage, setStage] = useState<'loading' | 'entry' | 'screening' | 'intake' | 'idle' | 'done'>('loading');
  const [categories, setCategories] = useState<TriggerCategory[]>([]);
  const [removalTarget, setRemovalTarget] = useState<TriggerCategory | null>(null);
  const [reused, setReused] = useState<Record<string, string[]>>({});
  const [expandedCloneId, setExpandedCloneId] = useState<string | null>(null);
  const [pendingQuestions, setPendingQuestions] = useState<IntakeQuestion[]>([]);
  const [validationOpen, setValidationOpen] = useState(false);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [viewCaseId, setViewCaseId] = useState<string | null>(null);
  const [viewingDoc, setViewingDoc] = useState<Document | null>(null);
  const [resumeMode, setResumeMode] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [activeFieldSetVersion, setActiveFieldSetVersion] = useState<number | null>(null);
  const [submittedInfo, setSubmittedInfo] = useState<{ analyst: string; slaTargetDate: string } | null>(null);
  const [checklistExpanded, setChecklistExpanded] = useState(false);
  const [docSourceIntegration, setDocSourceIntegration] = useState<Integration | null>(null);
  const [browseOpen, setBrowseOpen] = useState(false);

  const initRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Every script step in the demo flow is appended under a fixed, semantic id (not a counter) -
  // 'greeting', 'category-confirmed-bubble', a question's own q1/q2/q3 id, and so on. This set is
  // the append gate: a step's id can only ever land in the feed once per mount, so if a re-render
  // or a stray re-invocation calls push()/note() for a step that already happened, it's a no-op
  // instead of a second bubble. appendChatMessage is separately idempotent at the store level, so
  // the same guarantee holds even across a remount.
  const appendedIds = useRef(new Set<string>());
  const submitAttempt = useRef(0);

  function openAttachment(name: string, sizeKb: number) {
    const match = caseData?.documents.find((d) => d.name === name);
    setViewingDoc(match ?? { id: name, name, source: 'uploaded', version: 1, uploadedAt: new Date().toISOString(), sizeKb });
  }

  const push = useCallback(
    (item: Feed) => {
      // The append-once gate only applies to the demo script's fixed, one-time beats - a bubble,
      // a card. Transient UI (typing indicators, reply chips, the upload/extraction widgets) is
      // legitimately pushed and removed more than once per conversation under the same kind of
      // id, so gating those the same way would silently block the second occurrence.
      const idempotent = item.kind === 'bubble' || item.kind === 'classification' || item.kind === 'question' || item.kind === 'clone';
      if (idempotent) {
        if (appendedIds.current.has(item.id)) return;
        appendedIds.current.add(item.id);
      }
      setFeed((f) => [...f, item]);
      if (item.kind === 'bubble' && caseId) {
        appendChatMessage(caseId, { id: item.id, role: item.role, text: item.text, timestamp: new Date().toISOString(), attachments: item.attachments });
      }
    },
    [caseId],
  );
  const removeKind = useCallback((kind: Feed['kind']) => setFeed((f) => f.filter((x) => x.kind !== kind)), []);

  // Records a turn to the permanent transcript without adding a visual bubble - for PO actions
  // that already have their own on-screen representation elsewhere in the feed, so a second
  // bubble would just echo it. `key` is the same stable, semantic id `push` uses - same gate.
  const note = useCallback(
    (key: string, role: 'ai' | 'po', text: string) => {
      if (appendedIds.current.has(key)) return;
      appendedIds.current.add(key);
      if (!caseId) return;
      appendChatMessage(caseId, { id: key, role, text, timestamp: new Date().toISOString(), kind: 'note' });
    },
    [caseId],
  );

  const refresh = useCallback(async () => {
    if (!caseId) return;
    const c = await getCase(caseId);
    setCaseData(c);
    setCategories(c.categories);
    return c;
  }, [caseId]);

  useEffect(() => {
    getActiveFieldSetVersion().then(setActiveFieldSetVersion);
    // The effect of connecting a document source, made real rather than just described (§8.9
    // mod, §4) - intake offers browsing it alongside manual upload only while it's connected
    // and enabled (paused suspends the effect without losing the connection).
    getIntegrations().then((items) => {
      const source = items.find((i) => i.category === 'Document sources' && i.connected && i.enabled);
      setDocSourceIntegration(source ?? null);
    });
  }, []);

  useEffect(() => {
    if (!caseId || initRef.current) return;
    initRef.current = true;
    (async () => {
      let c: CaseDetail | undefined;
      try {
        c = await refresh();
      } catch {
        navigate('/queue');
        return;
      }
      if (!c) return;
      // Every id ever recorded to this case's transcript - replayed or not - counts as already
      // appended, so a handler re-firing later in this mount can't re-push a step from before it.
      for (const m of c.chatTranscript) appendedIds.current.add(m.id);
      // Notes are transcript-only records (a resolved question, a reuse pick) that already have
      // their own on-screen element elsewhere in a live session - replaying them as bubbles here
      // would show them a second time, with nothing to pair them back up with.
      const bubbleHistory = c.chatTranscript.filter((m) => m.kind !== 'note');
      if (bubbleHistory.length > 0) {
        // Resuming an existing draft - replay the stored transcript verbatim rather than pushing
        // fresh bubbles, so the greeting is never persisted twice. A transcript of only the
        // opening greeting means the PO hadn't picked a path yet - re-show entry-choice on top of
        // it instead of jumping to idle.
        setFeed(
          bubbleHistory.map((m) => ({ kind: 'bubble', id: m.id, role: m.role, text: m.text, attachments: m.attachments } as Feed)),
        );
        if (bubbleHistory.length === 1) {
          setFeed((f) => [...f, { kind: 'entry-choice', id: 'entry-choice' }]);
          setStage('entry');
        } else {
          setStage('idle');
        }
      } else {
        push({ kind: 'bubble', id: 'greeting', role: 'ai', text: "Hi Arun - before we dive in: is this a change you're confident needs an FCRM case, or would you like me to ask a couple of quick questions first?", animate: true });
        push({ kind: 'entry-choice', id: 'entry-choice' });
        setStage('entry');
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [feed]);

  // ---------------------------------------------------------------------
  // Entry
  // ---------------------------------------------------------------------
  function chooseScreening() {
    removeKind('entry-choice');
    setStage('screening');
    recordPathChoice(caseId!, 'screening');
    note('path-choice-note', 'po', 'Not sure if this needs FCRM review - asked for a few quick questions instead.');
    push({ kind: 'typing', id: 'typing' });
    getScreeningTurn(caseId!, '').then((turn) => {
      removeKind('typing');
      const m = turn.messages[0];
      push({ kind: 'bubble', id: m.id, role: 'ai', text: m.text, animate: true });
      if (m.replyChips) push({ kind: 'chips', id: nextId(), chips: m.replyChips, onSelect: handleScreeningReply });
    });
  }

  function handleScreeningReply(text: string) {
    removeKind('chips');
    push({ kind: 'bubble', id: nextId(), role: 'po', text });
    push({ kind: 'typing', id: 'typing' });
    getScreeningTurn(caseId!, text).then((turn) => {
      removeKind('typing');
      const m = turn.messages[0];
      push({ kind: 'bubble', id: m.id, role: 'ai', text: m.text, animate: true });
      if (turn.screeningOutcome === 'auto-clear') {
        push({ kind: 'auto-clear-approve', id: nextId() });
      } else if (m.replyChips) {
        push({ kind: 'chips', id: nextId(), chips: m.replyChips, onSelect: handleScreeningReply });
      }
    });
  }

  async function handleApproveAutoClear() {
    removeKind('auto-clear-approve');
    await approveAutoClear(caseId!);
    push({ kind: 'bubble', id: nextId(), role: 'ai', text: 'Logged. The light-touch record is saved to your queue as Closed - FCRM can see it in aggregate for periodic spot-check.', animate: true });
    toast('Auto-clear approved and logged.', 'success');
    setStage('done');
  }

  function chooseIntake() {
    removeKind('entry-choice');
    setStage('intake');
    recordPathChoice(caseId!, 'intake');
    note('path-choice-note', 'po', "I know this needs a case - let's build the submission.");
    push({ kind: 'bubble', id: 'intake-start-bubble', role: 'ai', text: "Let's start with what you already have. Upload any documents about this change - a business case, vendor contract, data-flow diagram - or just describe it and I'll work from that. I'll pull out what I can and only ask you about what's genuinely missing.", animate: true });
    push({ kind: 'upload', id: nextId(), files: STAGED_FILES, onUpload: handleUploadDone });
  }

  // ---------------------------------------------------------------------
  // Upload -> extraction/classification (parallel tracks - see ExtractionProgress)
  // ---------------------------------------------------------------------
  async function handleUploadDone() {
    removeKind('upload');
    await uploadFiles(caseId!, STAGED_FILES);
    push({ kind: 'bubble', id: 'upload-confirm-bubble', role: 'po', text: `Uploaded ${STAGED_FILES.length} file${STAGED_FILES.length === 1 ? '' : 's'}`, attachments: STAGED_FILES });
    push({ kind: 'extraction-progress', id: nextId(), onDone: handleExtractionDone });
  }

  async function handleBrowseImport(files: FileRef[]) {
    if (!docSourceIntegration || files.length === 0) return;
    setBrowseOpen(false);
    removeKind('upload');
    await importIntegrationDocuments(caseId!, docSourceIntegration.id, files);
    push({
      kind: 'bubble',
      id: 'upload-confirm-bubble-integration',
      role: 'po',
      text: `Imported ${files.length} file${files.length === 1 ? '' : 's'} from ${docSourceIntegration.name}`,
      attachments: files,
    });
    push({ kind: 'extraction-progress', id: nextId(), onDone: handleExtractionDone });
  }

  async function handleExtractionDone() {
    removeKind('extraction-progress');
    const [extraction, classification] = await Promise.all([extractFields(caseId!, STAGED_FILES), classifyCategories(caseId!)]);
    const totalForProposed = mergedRequiredFields(classification.proposals.map((p) => p.category)).length;
    await setCompletion(caseId!, totalForProposed > 0 ? Math.round((extraction.fields.length / totalForProposed) * 100) : 0);
    // Case title auto-generates once there's enough signal - §6.6.
    await updateCaseTitle(caseId!, isDemo ? DEMO_CASE_TITLE : summarizeTitle(extraction.fields[0]?.value));
    await refresh();
    const n = extraction.fields.length;
    push({
      kind: 'bubble',
      id: 'extraction-done-bubble',
      role: 'ai',
      text: `Got it - I've pulled ${n} field${n === 1 ? '' : 's'} from what you shared. Take a look at the checklist on the right. I also have a category proposal for you.`,
      animate: true,
    });
    push({ kind: 'classification', id: 'classification-card', proposals: classification.proposals, removed: [], added: [], confirmed: null });
  }

  // ---------------------------------------------------------------------
  // Category confirmation
  // ---------------------------------------------------------------------
  function handleRemoveCategory(cat: TriggerCategory) {
    setRemovalTarget(cat);
  }

  async function handleRemoveConfirm(reason: string) {
    if (!removalTarget) return;
    await removeCategoryTag(caseId!, removalTarget, reason);
    setFeed((f) =>
      f.map((item) => (item.kind === 'classification' ? { ...item, removed: [...item.removed, removalTarget] } : item)),
    );
    setRemovalTarget(null);
    toast('Tag removed - reason logged to the Activity Log.', 'default');
  }

  // Browse all categories: selecting one beyond what the AI proposed needs no reason - it's the
  // PO's own pick, not an override. Un-picking it before confirming needs no reason either, since
  // nothing was ever overridden.
  async function handleAddCategory(cat: TriggerCategory) {
    await addCategoryTag(caseId!, cat);
    setFeed((f) => f.map((item) => (item.kind === 'classification' ? { ...item, added: [...item.added, cat] } : item)));
  }

  async function handleRemoveAddedCategory(cat: TriggerCategory) {
    await removeAddedCategoryTag(caseId!, cat);
    setFeed((f) => f.map((item) => (item.kind === 'classification' ? { ...item, added: item.added.filter((c) => c !== cat) } : item)));
  }

  async function handleConfirmCategories() {
    const item = feed.find((f) => f.kind === 'classification') as Extract<Feed, { kind: 'classification' }> | undefined;
    const proposedKept = CLASSIFICATION_PROPOSALS.map((p) => p.category).filter((c) => !(item?.removed ?? []).includes(c));
    const confirmed = [...proposedKept, ...(item?.added ?? [])];
    await confirmCategories(caseId!, confirmed);
    note('category-confirm-note', 'po', `Confirmed categories: ${confirmed.join(', ')}`);
    // The proposal card's job is done once confirmed - collapse it rather than leaving the full
    // expanded list (with its now-stale remove/browse controls) sitting in the transcript. The
    // bubble below is the single line it collapses to.
    removeKind('classification');
    setCategories(confirmed);
    const c = await refresh();

    const totalRequired = mergedRequiredFields(confirmed).length;
    if (c) await setCompletion(caseId!, c.requiredFieldCount > 0 ? Math.round((c.requiredFieldsFilled / c.requiredFieldCount) * 100) : 0);
    await refresh();

    push({
      kind: 'bubble',
      id: 'category-confirmed-bubble',
      role: 'ai',
      text: isDemo
        ? `Categories confirmed: ${confirmed.join(', ')}. Required-field set is ${totalRequired} fields - 9 core plus what ${confirmed.length === 1 ? 'that category adds' : 'those categories add'}.`
        : `Categories confirmed: ${confirmed.join(', ')}. Required-field set is now ${totalRequired} fields.`,
      animate: true,
    });

    setTimeout(() => beginQuestions(), 800);
  }

  // ---------------------------------------------------------------------
  // Question rounds
  // ---------------------------------------------------------------------
  function beginQuestions() {
    setStage('idle');
    if (isDemo) {
      // No standalone "Two things I need next." preamble - it named a count and nothing else,
      // and the question card right below it already shows both questions in full.
      push({ kind: 'question', id: 'round1-questions', questions: ROUND_1_QUESTIONS });
      // The question card carries the actual text; the transcript record needs it too, per-
      // question so the record shows precisely what was asked.
      for (const q of ROUND_1_QUESTIONS) note(`round1-q-note-${q.id}`, 'ai', q.questionText);
      setPendingQuestions(ROUND_1_QUESTIONS);
    } else {
      push({ kind: 'bubble', id: nextId(), role: 'ai', text: 'Anything else you can tell me, or would you like to try submitting?', animate: true });
      push({ kind: 'chips', id: nextId(), chips: ['Try submitting', 'Add more detail'], onSelect: handleGenericSend });
    }
  }

  async function handleQuestionAnswer(fieldId: string, value: string) {
    setPendingQuestions((qs) => qs.filter((q) => q.fieldId !== fieldId));
    // Covers both answer routes - the inline box and the global chat input both call this, and
    // neither pushes its own bubble, so this is the only place the answer reaches the transcript.
    note(`answer-note-${fieldId}`, 'po', value);
    await applyFieldUpdates(caseId!, [{ fieldId, value }]);
    await setCompletion(caseId!, await liveCompletionPct(caseId!));
    const c = await refresh();

    if (!isDemo) return;

    if (fieldId === ROUND_2_QUESTION.fieldId) {
      // Round 2's single question - the threshold is crossed here (12/15 = 80%). Submit lives
      // in the header only - no in-chat button here, so there's never two controls that might
      // not mean the same thing.
      push({ kind: 'typing', id: 'typing' });
      setTimeout(() => {
        removeKind('typing');
        const blankLabels = c?.fields.filter((f) => !f.value).map((f) => f.label) ?? [];
        const blankText =
          blankLabels.length > 0
            ? `${blankLabels.join(', ')} ${blankLabels.length === 1 ? 'is' : 'are'} still blank.`
            : 'Every required field is filled.';
        push({
          kind: 'bubble',
          id: 'threshold-status-bubble',
          role: 'ai',
          text: `You're at ${c?.completionPct ?? 80}%, above the ${c?.threshold ?? 80}% threshold. ${blankText} Add it below, or submit now and the analyst will raise it if they need it.`,
          animate: true,
        });
      }, 600);
      return;
    }

    const round1Done = ROUND_1_QUESTIONS.every((q) => c?.fields.find((f) => f.fieldId === q.fieldId)?.value);
    if (round1Done) {
      push({ kind: 'typing', id: 'typing' });
      setTimeout(() => {
        removeKind('typing');
        push({
          kind: 'bubble',
          id: 'round2-transition-bubble',
          role: 'ai',
          text: "One more thing, and a couple of related cases worth a look while you're at it.",
          animate: true,
        });
        push({ kind: 'question', id: 'round2-question', questions: [ROUND_2_QUESTION] });
        note(`round1-q-note-${ROUND_2_QUESTION.id}`, 'ai', ROUND_2_QUESTION.questionText);
        setPendingQuestions([ROUND_2_QUESTION]);
        handleShowClone();
      }, 700);
    }
  }

  // ---------------------------------------------------------------------
  // Clone suggestion
  // ---------------------------------------------------------------------
  async function handleShowClone() {
    const suggestions = await getCloneSuggestions(caseId!);
    if (suggestions.length === 0) return;
    push({ kind: 'clone', id: 'clone-card' });
  }

  function toggleCloneField(sourceCaseId: string, fieldId: string) {
    setReused((r) => {
      const current = r[sourceCaseId] ?? [];
      const next = current.includes(fieldId) ? current.filter((f) => f !== fieldId) : [...current, fieldId];
      return { ...r, [sourceCaseId]: next };
    });
  }

  async function handleCloneContinue() {
    removeKind('clone');
    let total = 0;
    const takenLines: string[] = [];
    for (const [sourceCaseId, fieldIds] of Object.entries(reused)) {
      if (fieldIds.length === 0) continue;
      await reuseCloneFields(caseId!, sourceCaseId, fieldIds);
      total += fieldIds.length;
      const suggestion = CLONE_SUGGESTIONS.find((s) => s.caseId === sourceCaseId);
      const labels = fieldIds.map((fid) => suggestion?.fields.find((f) => f.fieldId === fid)?.label ?? fid);
      takenLines.push(`from ${sourceCaseId}: ${labels.join(', ')}`);
    }
    if (total > 0) {
      const pct = await liveCompletionPct(caseId!);
      await setCompletion(caseId!, pct);
      await refresh();
      // The card's interaction is over - it collapses to this AI acknowledgement rather than
      // staying expanded with now-inert controls. The PO's own turn (what was taken, from where)
      // is recorded alongside it, transcript-only, so the record shows both sides of the moment.
      note('reuse-po-note', 'po', `Reused ${total} field${total === 1 ? '' : 's'} ${takenLines.join('; ')}.`);
      push({
        kind: 'bubble',
        id: 'reuse-ack-bubble',
        role: 'ai',
        text: `Got it - ${total} field${total === 1 ? '' : 's'} landed (${takenLines.join('; ')}). You're at ${pct}% now.`,
        animate: true,
      });
      toast(`Reused ${total} field${total === 1 ? '' : 's'} from prior cases.`, 'success');
    }
  }

  // ---------------------------------------------------------------------
  async function handleGenericSend(text: string) {
    removeKind('chips');
    push({ kind: 'bubble', id: nextId(), role: 'po', text });
    if (/submit/i.test(text)) {
      handleSubmit();
      return;
    }
    push({ kind: 'typing', id: 'typing' });
    const turn = await getIntakeTurn(caseId!, text);
    removeKind('typing');
    const m = turn.messages[0];
    push({ kind: 'bubble', id: m.id, role: 'ai', text: m.text, animate: true });
    if (m.replyChips) push({ kind: 'chips', id: nextId(), chips: m.replyChips, onSelect: handleGenericSend });
  }

  function handleFreeTextSend() {
    const text = input.trim();
    if (!text) return;
    setInput('');
    if (stage === 'screening') {
      handleScreeningReply(text);
    } else if (pendingQuestions.length > 0) {
      // The chat input answers the same next pending question an inline answer box would -
      // neither route is primary. The question card itself shows the resolved answer, so no
      // separate bubble here - that would just echo the same text a second time.
      handleQuestionAnswer(pendingQuestions[0].fieldId, text);
    } else if (resumeMode) {
      handleResumeReply(text);
    } else {
      handleGenericSend(text);
    }
  }

  // ---------------------------------------------------------------------
  // Validation & submit
  // ---------------------------------------------------------------------
  async function handleSubmit() {
    removeKind('chips');
    setSubmitting(true);
    // Keyed per attempt, not fixed - a failed validation can be retried after reopen-and-resume,
    // and each real attempt belongs in the record. A stable key here would silently swallow the
    // second attempt's outcome.
    submitAttempt.current += 1;
    const attempt = submitAttempt.current;
    note(`submit-po-note-${attempt}`, 'po', 'Submitted for validation.');
    const result = await runValidation(caseId!);
    setSubmitting(false);
    setValidationResult(result);
    setValidationOpen(true);
    // The transcript's last word - the validation run and what it found, whatever the outcome.
    const stepsSummary = result.steps.map((s) => `${s.label}: ${s.passed ? 'passed' : 'failed'}`).join(' · ');
    note(`validation-outcome-note-${attempt}`, 'ai', `Submission Validation Agent: ${result.passed ? 'all checks passed' : 'did not pass'} - ${result.completionPct}% complete. ${stepsSummary}`);
  }

  async function handleReopen() {
    setValidationOpen(false);
    await reopenIntake(caseId!);
    setResumeMode(true);
    push({
      kind: 'bubble',
      id: nextId(),
      role: 'ai',
      text: isDemo
        ? "Picking up right where we left off - I just need three things: the vendor's legal name and licensing jurisdiction, its data-retention scope, and the due-diligence questionnaire. Anything you can give me now?"
        : `Picking up where we left off - I still need: ${(validationResult?.missingFieldLabels ?? []).join(' · ')}.`,
      animate: true,
    });
    push({ kind: 'chips', id: nextId(), chips: ['I have all three now'], onSelect: handleResumeReply });
  }

  async function handleResumeReply(text: string) {
    removeKind('chips');
    push({ kind: 'bubble', id: nextId(), role: 'po', text });
    if (isDemo) {
      await applyFieldUpdates(caseId!, RESUME_FIELD_UPDATES);
      await refresh();
      push({ kind: 'upload', id: nextId(), files: [RESUME_UPLOAD_FILE], onUpload: handleResumeUploadDone });
    } else {
      push({ kind: 'typing', id: 'typing' });
      setTimeout(() => {
        removeKind('typing');
        push({ kind: 'bubble', id: nextId(), role: 'ai', text: 'Thanks - noted. Try submitting again when ready.', animate: true });
      }, 600);
    }
  }

  async function handleResumeUploadDone() {
    removeKind('upload');
    await uploadFiles(caseId!, [RESUME_UPLOAD_FILE]);
    await applyFieldUpdates(caseId!, [{ fieldId: 'supporting_documents', value: RESUME_UPLOAD_FILE.name }]);
    await setCompletion(caseId!, await liveCompletionPct(caseId!));
    await refresh();
    setResumeMode(false);
    push({ kind: 'bubble', id: nextId(), role: 'po', text: `Uploaded ${RESUME_UPLOAD_FILE.name}`, attachments: [RESUME_UPLOAD_FILE] });
    push({ kind: 'bubble', id: nextId(), role: 'ai', text: "You're over the completion threshold and every always-required field is filled - ready to submit from the header whenever you are.", animate: true });
  }

  async function handleValidationClose() {
    setValidationOpen(false);
    if (validationResult?.passed) {
      setSubmitting(true);
      const result = await submitCase(caseId!);
      setSubmitting(false);
      setSubmittedInfo({ analyst: result.analyst ?? '-', slaTargetDate: result.slaTargetDate ?? '-' });
      setStage('done');
    }
  }

  // ---------------------------------------------------------------------
  const headerTitle = caseData?.title ?? 'New case';
  const expandedSuggestion = expandedCloneId ? (CLONE_SUGGESTIONS.find((s) => s.caseId === expandedCloneId) ?? null) : null;

  if (stage === 'loading' || !caseData) {
    return <div className="p-8 text-text-muted text-sm">Loading…</div>;
  }

  if (stage === 'done' && submittedInfo) {
    return (
      <div className="max-w-[640px] mx-auto px-8 py-16 flex flex-col items-center text-center gap-4">
        <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="w-16 h-16 rounded-full bg-verdant-2 flex items-center justify-center">
          <Icon name="check" size={28} className="text-teal" strokeWidth={2.5} />
        </motion.div>
        <h1 className="text-[22px] font-bold text-text-primary">{caseData.id} submitted</h1>
        <p className="text-[14px] text-text-secondary">
          Assigned to <strong>{submittedInfo.analyst}</strong>. SLA target: <strong>{submittedInfo.slaTargetDate}</strong>. Status: Under Assessment.
        </p>
        <div className="flex gap-3 mt-2">
          <Button variant="primary" onClick={() => navigate(`/case/${caseData.id}`)}>
            View case
          </Button>
          <Button variant="secondary" onClick={() => navigate('/queue')}>
            Back to Queue
          </Button>
        </div>
      </div>
    );
  }

  if (stage === 'done') {
    return (
      <div className="max-w-[640px] mx-auto px-8 py-16 flex flex-col items-center text-center gap-4">
        <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="w-16 h-16 rounded-full bg-verdant-2 flex items-center justify-center">
          <Icon name="check" size={28} className="text-teal" strokeWidth={2.5} />
        </motion.div>
        <h1 className="text-[22px] font-bold text-text-primary">Cleared</h1>
        <p className="text-[14px] text-text-secondary">This light-touch record is saved to your queue as Closed.</p>
        <Button variant="secondary" onClick={() => navigate('/queue')}>
          Back to Queue
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-64px)]">
      <div className="flex items-center justify-between px-6 py-3.5 border-b border-border bg-white shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-[12px] font-mono text-text-faint shrink-0">{caseData.id}</span>
          <span className="text-[14px] font-semibold text-text-primary truncate">{headerTitle}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {stage === 'intake' || stage === 'idle' ? (
            <Button size="sm" variant="primary" onClick={handleSubmit} loading={submitting}>
              Submit
            </Button>
          ) : null}
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        {/* display:none, not conditional rendering - the scroll container's DOM node has to
            survive expand/collapse for its scrollTop to survive it too. */}
        <div className={`flex-1 flex-col min-w-0 bg-white ${checklistExpanded ? 'hidden' : 'flex'}`} style={checklistExpanded ? undefined : { flexBasis: '60%' }}>
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6 flex flex-col gap-3.5">
            {feed.map((item) => {
              switch (item.kind) {
                case 'bubble':
                  return (
                    <ChatBubble
                      key={item.id}
                      role={item.role}
                      text={item.text}
                      animate={item.animate}
                      attachments={item.attachments}
                      onAttachmentClick={openAttachment}
                    />
                  );
                case 'typing':
                  return <TypingIndicator key={item.id} />;
                case 'chips':
                  return <ReplyChips key={item.id} chips={item.chips} onSelect={item.onSelect} />;
                case 'entry-choice':
                  return (
                    <div key={item.id} className="flex flex-col gap-2.5 max-w-[86%]">
                      <button
                        onClick={chooseScreening}
                        className="text-left px-4 py-3 rounded-lg border border-border-strong bg-white hover:border-carbon-moss hover:bg-surface-tertiary transition-colors cursor-pointer"
                      >
                        <span className="text-[13.5px] font-semibold text-text-primary">Not sure if this needs FCRM review?</span>
                        <p className="text-[12px] text-text-muted mt-0.5">A few quick questions, no documents required.</p>
                      </button>
                      <button
                        onClick={chooseIntake}
                        className="text-left px-4 py-3 rounded-lg border border-border-strong bg-white hover:border-carbon-moss hover:bg-surface-tertiary transition-colors cursor-pointer"
                      >
                        <span className="text-[13.5px] font-semibold text-text-primary">Start intake</span>
                        <p className="text-[12px] text-text-muted mt-0.5">I know this needs a case - let's build the submission.</p>
                      </button>
                    </div>
                  );
                case 'upload':
                  return (
                    <div key={item.id} className="flex flex-col gap-2 max-w-[86%]">
                      <UploadWidget files={item.files} onUpload={item.onUpload} />
                      {docSourceIntegration && (
                        <button
                          onClick={() => setBrowseOpen(true)}
                          className="self-start text-[12px] font-semibold text-carbon-moss hover:underline cursor-pointer flex items-center gap-1"
                        >
                          <Icon name="search" size={13} />
                          Browse {docSourceIntegration.name} instead
                        </button>
                      )}
                    </div>
                  );
                case 'extraction-progress':
                  return <ExtractionProgress key={item.id} onDone={item.onDone} />;
                case 'classification':
                  return (
                    <ClassificationCard
                      key={item.id}
                      proposals={item.proposals}
                      removedCategories={item.removed}
                      addedCategories={item.added}
                      confirmed={item.confirmed}
                      onRemove={handleRemoveCategory}
                      onAdd={handleAddCategory}
                      onRemoveAdded={handleRemoveAddedCategory}
                      onConfirm={handleConfirmCategories}
                    />
                  );
                case 'question':
                  return (
                    <IntakeQuestionCard
                      key={item.id}
                      questions={item.questions}
                      answers={Object.fromEntries(
                        item.questions.map((q) => [q.fieldId, caseData.fields.find((f) => f.fieldId === q.fieldId)?.value ?? undefined]),
                      ) as Record<string, string>}
                      onAnswer={handleQuestionAnswer}
                    />
                  );
                case 'clone':
                  return (
                    <CloneSuggestionCard
                      key={item.id}
                      suggestions={CLONE_SUGGESTIONS}
                      reused={reused}
                      onExpand={(id) => setExpandedCloneId(id)}
                      onContinue={handleCloneContinue}
                    />
                  );
                case 'auto-clear-approve':
                  return (
                    <div key={item.id} className="pl-1">
                      <Button variant="primary" size="sm" onClick={handleApproveAutoClear}>
                        Approve light-touch record
                      </Button>
                    </div>
                  );
                default:
                  return null;
              }
            })}
          </div>

          <div className="border-t border-border px-6 py-4 flex gap-2.5 shrink-0 items-center">
            <button onClick={() => window.open('/queue', '_blank')} className="text-[12px] font-semibold text-carbon-moss hover:underline cursor-pointer whitespace-nowrap flex items-center gap-1">
              <Icon name="search" size={13} />
              Browse related cases
            </button>
            <Input
              placeholder="Type a message…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleFreeTextSend()}
              className="flex-1"
            />
            <Button variant="primary" size="md" icon={<Icon name="send" size={15} />} onClick={handleFreeTextSend}>
              Send
            </Button>
          </div>
        </div>

        <div
          className={checklistExpanded ? 'flex-1 w-full' : 'shrink-0 max-w-[440px] min-w-[340px]'}
          style={checklistExpanded ? undefined : { flexBasis: '40%' }}
        >
          <ChecklistPanel
            categories={categories}
            fields={caseData.fields}
            optionalCount={caseData.optionalFields.length}
            completionPct={caseData.completionPct}
            threshold={caseData.threshold}
            fieldSetVersion={caseData.fieldSetVersion}
            activeFieldSetVersion={activeFieldSetVersion}
            expanded={checklistExpanded}
            onToggleExpand={() => setChecklistExpanded((e) => !e)}
            onEditField={async (fieldId, value) => {
              await editFieldValue(caseId!, fieldId, value);
              refresh();
            }}
          />
        </div>
      </div>

      <CategoryReasonModal
        key={removalTarget ?? 'none'}
        category={removalTarget}
        suggestedReason={removalTarget === 'New Customer Segment' ? CATEGORY_REMOVAL_SUGGESTED_REASON : undefined}
        onCancel={() => setRemovalTarget(null)}
        onSubmit={handleRemoveConfirm}
      />
      <CloneReuseModal
        suggestion={expandedSuggestion}
        currentFields={caseData.fields}
        reusedFieldIds={expandedCloneId ? reused[expandedCloneId] ?? [] : []}
        onToggleField={(fieldId) => expandedCloneId && toggleCloneField(expandedCloneId, fieldId)}
        onClose={() => setExpandedCloneId(null)}
        onViewFullCase={(id) => {
          setExpandedCloneId(null);
          setViewCaseId(id);
        }}
      />
      <ValidationModal open={validationOpen} result={validationResult} onClose={handleValidationClose} onReopen={handleReopen} />
      <CaseViewModal caseId={viewCaseId} onClose={() => setViewCaseId(null)} />
      <DocumentViewModal doc={viewingDoc} onClose={() => setViewingDoc(null)} />
      <BrowseSourceModal integration={browseOpen ? docSourceIntegration : null} onClose={() => setBrowseOpen(false)} onImport={handleBrowseImport} />
    </div>
  );
}
