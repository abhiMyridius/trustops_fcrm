import { useEffect, useState } from 'react';
import type { AgentConfig, AgentName, CaseDetail, ChatMessage, Document } from '@/types';
import { getAgentConfigs } from '@/services/api';
import { MODEL_REFERENCE, MODEL_VERSIONS } from '@/mocks/seed/models';
import { DEMO_CASE_ID, ROUND_1_QUESTIONS } from '@/mocks/scripts/intakeScript';
import { AGENT_META } from '@/pages/AIConfiguration';
import { Card } from '@/components/ui/Card';
import { Icon } from '@/components/ui/Icon';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { formatDateTime } from '@/utils/format';
import { AttachmentChip } from '@/pages/NewCase/ChatBubble';
import { DocumentViewModal } from './DocumentViewModal';

const AGENT_ORDER: AgentName[] = ['Screening', 'Intake', 'Extraction', 'Classification', 'Submission Validation'];

interface AgentRow {
  agent: AgentName;
  ran: boolean;
  statusLabel: string;
  produced: string;
  timestamp?: string;
}

// Every agent gets a row, including ones that never ran - a silent gap in the list would be
// indistinguishable from a failure, and the PO was always offered screening even when they
// declined it in favor of full intake.
function deriveAgentRows(caseData: CaseDetail): AgentRow[] {
  const log = caseData.activityLog;
  const find = (type: string) => log.filter((e) => e.type === type);
  const pathEvent = find('path_chosen').at(-1);
  const screeningChosen = pathEvent?.detail === 'screening';
  const intakeChosen = pathEvent?.detail === 'intake';

  const rows: AgentRow[] = [];

  if (screeningChosen) {
    const cleared = find('auto_cleared').at(-1);
    rows.push({
      agent: 'Screening',
      ran: true,
      statusLabel: 'Ran',
      produced: cleared ? 'PO completed screening - auto-cleared, logged as a light-touch record' : 'PO completed screening',
      timestamp: (cleared ?? pathEvent)?.timestamp,
    });
  } else if (intakeChosen) {
    rows.push({ agent: 'Screening', ran: false, statusLabel: 'Offered, declined', produced: 'PO chose full intake', timestamp: pathEvent?.timestamp });
  } else {
    rows.push({ agent: 'Screening', ran: false, statusLabel: 'Not run', produced: 'No record of the entry choice for this case.' });
  }

  if (intakeChosen) {
    const produced =
      caseData.id === DEMO_CASE_ID
        ? `Conducted 2 question rounds, ${ROUND_1_QUESTIONS.length + 1} questions`
        : 'Conducted the intake conversation';
    rows.push({ agent: 'Intake', ran: true, statusLabel: 'Ran', produced, timestamp: pathEvent?.timestamp });
  } else if (screeningChosen) {
    rows.push({ agent: 'Intake', ran: false, statusLabel: 'Not run', produced: 'PO went through screening instead.' });
  } else {
    rows.push({ agent: 'Intake', ran: false, statusLabel: 'Not run', produced: 'No record of the entry choice for this case.' });
  }

  const lastExtraction = find('extraction_proposed').at(-1);
  rows.push(
    lastExtraction
      ? { agent: 'Extraction', ran: true, statusLabel: 'Ran', produced: lastExtraction.summary, timestamp: lastExtraction.timestamp }
      : { agent: 'Extraction', ran: false, statusLabel: 'Not run', produced: 'No documents extracted for this case.' },
  );

  const lastClass = find('classification_proposed').at(-1);
  if (lastClass) {
    const confirmed = find('categories_confirmed').at(-1);
    const proposedText = lastClass.detail ?? 'category tags proposed';
    rows.push({
      agent: 'Classification',
      ran: true,
      statusLabel: 'Ran',
      produced: `${proposedText}, ${confirmed ? `${caseData.categories.length} confirmed` : 'not yet confirmed'}`,
      timestamp: lastClass.timestamp,
    });
  } else {
    rows.push({ agent: 'Classification', ran: false, statusLabel: 'Not run', produced: 'No category proposal for this case.' });
  }

  const lastVal = [...find('validation_passed'), ...find('validation_failed')].sort((a, b) => (a.timestamp > b.timestamp ? 1 : -1)).at(-1);
  rows.push(
    lastVal
      ? {
          agent: 'Submission Validation',
          ran: true,
          statusLabel: lastVal.type === 'validation_passed' ? 'Ran' : 'Ran, failed',
          produced: `${lastVal.detail ?? 'checks run'}, ${lastVal.type === 'validation_passed' ? 'all passed' : 'did not pass'}`,
          timestamp: lastVal.timestamp,
        }
      : { agent: 'Submission Validation', ran: false, statusLabel: 'Not run', produced: 'Not submitted for validation yet.' },
  );

  return rows.sort((a, b) => AGENT_ORDER.indexOf(a.agent) - AGENT_ORDER.indexOf(b.agent));
}

function AgentActivityRow({ row, config, onViewActivityLog }: { row: AgentRow; config?: AgentConfig; onViewActivityLog: () => void }) {
  const [open, setOpen] = useState(false);
  const meta = AGENT_META[row.agent];
  const model = config ? MODEL_REFERENCE.find((m) => m.id === config.modelId) : undefined;
  const modelVersion = model && config ? `${model.name} · ${MODEL_VERSIONS[config.modelId] ?? 'unversioned'}` : undefined;

  return (
    <div className="border border-border-hairline rounded-md overflow-hidden">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-surface-tertiary transition-colors cursor-pointer">
        <Icon name={meta.icon} size={16} className="text-carbon-moss shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[13.5px] font-semibold text-text-primary">{row.agent}</span>
            <Badge tone={row.ran ? 'success' : 'default'}>{row.statusLabel}</Badge>
          </div>
          <p className="text-[12.5px] text-text-muted mt-0.5">{row.produced}</p>
        </div>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={16} className="text-text-faint shrink-0" />
      </button>
      {open && (
        <div className="px-4 py-3 border-t border-border-hairline bg-surface-tertiary flex flex-col gap-3 text-[12.5px]">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
            <div>
              <span className="text-text-muted">Model: </span>
              <span className="font-semibold text-text-primary">{model?.name ?? 'Not configured'}</span>
            </div>
            <div>
              <span className="text-text-muted">Effort: </span>
              <span className="font-semibold text-text-primary">{config?.effort ?? '-'}</span>
            </div>
            <div>
              <span className="text-text-muted">Model version: </span>
              <span className="font-semibold text-text-primary">{modelVersion ?? '-'}</span>
            </div>
            <div>
              <span className="text-text-muted">Timestamp: </span>
              <span className="font-semibold text-text-primary">{row.timestamp ? formatDateTime(row.timestamp) : 'Not yet run'}</span>
            </div>
          </div>
          {row.ran && (
            <Button size="sm" variant="secondary" onClick={onViewActivityLog} className="self-start">
              View in Activity Log
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function TranscriptList({
  messages,
  onAttachmentClick,
  className,
}: {
  messages: ChatMessage[];
  onAttachmentClick: (name: string, sizeKb: number) => void;
  className?: string;
}) {
  return (
    <div className={`flex flex-col gap-3 ${className ?? ''}`}>
      {messages.map((m) => (
        <div key={m.id} className={`flex flex-col gap-1.5 ${m.role === 'po' ? 'items-end' : 'items-start'}`}>
          <div
            className={`max-w-[75%] px-3.5 py-2.5 rounded-xl text-[13px] leading-relaxed whitespace-pre-wrap ${
              m.role === 'po' ? 'bg-carbon-moss text-white rounded-br-sm' : 'bg-surface-secondary border border-border-hairline text-text-primary rounded-bl-sm'
            }`}
          >
            {m.text}
          </div>
          {m.attachments && m.attachments.length > 0 && (
            <div className="flex flex-col gap-1.5">
              {m.attachments.map((a) => (
                <AttachmentChip key={a.name} name={a.name} sizeKb={a.sizeKb} onClick={() => onAttachmentClick(a.name, a.sizeKb)} />
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function AIAssistanceTab({ caseData, onViewActivityLog }: { caseData: CaseDetail; onViewActivityLog: () => void }) {
  const [configs, setConfigs] = useState<AgentConfig[] | null>(null);
  const [viewingDoc, setViewingDoc] = useState<Document | null>(null);
  const [transcriptExpanded, setTranscriptExpanded] = useState(false);

  useEffect(() => {
    getAgentConfigs().then(setConfigs);
  }, []);

  function openAttachment(name: string, sizeKb: number) {
    const match = caseData.documents.find((d) => d.name === name);
    setViewingDoc(match ?? { id: name, name, source: 'uploaded', version: 1, uploadedAt: caseData.createdAt, sizeKb });
  }

  const rows = deriveAgentRows(caseData);

  return (
    <div className="flex flex-col gap-6">
      <Card material="flat" className="p-5">
        <span className="text-sm font-bold text-text-primary block">Agent activity</span>
        <p className="text-[12.5px] text-text-muted mb-3 mt-0.5">Every agent in the intake pipeline, including ones that didn't run on this case.</p>
        <div className="flex flex-col gap-2">
          {rows.map((row) => (
            <AgentActivityRow key={row.agent} row={row} config={configs?.find((c) => c.agent === row.agent)} onViewActivityLog={onViewActivityLog} />
          ))}
        </div>
      </Card>

      <Card material="flat" className="p-5">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-bold text-text-primary">Intake chat transcript</span>
          {caseData.chatTranscript.length > 0 && (
            <button
              onClick={() => setTranscriptExpanded(true)}
              className="flex items-center gap-1.5 text-[12.5px] font-semibold text-carbon-moss hover:underline cursor-pointer"
            >
              <Icon name="expand" size={13} />
              Expand
            </button>
          )}
        </div>
        {caseData.chatTranscript.length === 0 ? (
          <p className="text-[13px] text-text-faint">No transcript recorded.</p>
        ) : (
          <TranscriptList messages={caseData.chatTranscript} onAttachmentClick={openAttachment} className="max-h-[420px] overflow-y-auto pr-2 -mr-2" />
        )}
      </Card>

      <Modal open={transcriptExpanded} onClose={() => setTranscriptExpanded(false)} title="Intake chat transcript" width={720}>
        <TranscriptList messages={caseData.chatTranscript} onAttachmentClick={openAttachment} className="max-h-[75vh] overflow-y-auto pr-2 -mr-2" />
      </Modal>

      <DocumentViewModal doc={viewingDoc} onClose={() => setViewingDoc(null)} />
    </div>
  );
}
