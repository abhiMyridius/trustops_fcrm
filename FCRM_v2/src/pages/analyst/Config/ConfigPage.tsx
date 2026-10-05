import { useEffect, useState } from 'react';
import { getScoringModelVersions } from '@/services/analystApi';
import type { ScoringModelVersion } from '@/types/analyst';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Modal } from '@/components/ui/Modal';
import { CategoriesFieldsSection } from './CategoriesFieldsSection';
import { ClassificationRulesSection } from './ClassificationRulesSection';
import { ControlInventorySection } from './ControlInventorySection';
import { PolicyCorpusSection } from './PolicyCorpusSection';
import { ScoringModelSection } from './ScoringModelSection';
import { RulesRoutingSection } from './RulesRoutingSection';
import { JurisdictionRiskSection } from './JurisdictionRiskSection';
import { AIAgentsSection } from './AIAgentsSection';
import { WorkingSetProvider, useWorkingSet } from './WorkingSetContext';

type SectionId = 'categories' | 'classification' | 'scoring' | 'controls' | 'policy' | 'routing' | 'jurisdiction' | 'agents';

const SECTIONS: { id: SectionId; label: string }[] = [
  { id: 'categories', label: 'Categories & Fields' },
  { id: 'classification', label: 'Classification Rules' },
  { id: 'scoring', label: 'Scoring Model' },
  { id: 'controls', label: 'Control Inventory' },
  { id: 'policy', label: 'Policy Corpus' },
  { id: 'routing', label: 'Rules & Routing' },
  { id: 'jurisdiction', label: 'Jurisdiction Risk List' },
  { id: 'agents', label: 'AI Agents' },
];

// -----------------------------------------------------------------------
// Working-set banner (§1.1) - lives at the shell level, not inside any one section, so it stays
// visible (and its Publish/Discard stay actionable) no matter which Config section is showing.
// -----------------------------------------------------------------------
function WorkingSetBanner({ onNavigateToSection }: { onNavigateToSection: (id: SectionId) => void }) {
  const { changes, revertChange, requestPublish, requestDiscard } = useWorkingSet();
  const [expanded, setExpanded] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  if (changes.length === 0) return null;

  const sectionLabels = Array.from(new Set(changes.map((c) => c.sectionLabel)));
  const sectionIds = Array.from(new Set(changes.map((c) => c.sectionId)));

  function publish() {
    sectionIds.forEach((id) => {
      onNavigateToSection(id as SectionId);
      requestPublish(id);
    });
  }
  function discard() {
    sectionIds.forEach((id) => requestDiscard(id));
    setConfirmDiscard(false);
  }

  return (
    <div className="sticky top-0 z-10 flex flex-col gap-2 bg-white border border-carbon-moss rounded-md px-4 py-3 shadow-md">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <button onClick={() => setExpanded((e) => !e)} className="flex items-center gap-2 text-left cursor-pointer">
          <Icon name={expanded ? 'chevron-down' : 'chevron-right'} size={14} className="text-text-faint shrink-0" />
          <span className="text-[13px] text-text-primary font-medium">
            {changes.length} unsaved change{changes.length === 1 ? '' : 's'} · {sectionLabels.join(', ')}
          </span>
        </button>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => setConfirmDiscard(true)}>
            Discard
          </Button>
          <Button size="sm" variant="primary" onClick={publish}>
            Publish
          </Button>
        </div>
      </div>
      {expanded && (
        <div className="flex flex-col divide-y divide-border-hairline pl-6">
          {changes.map((c) => (
            <div key={c.id} className="flex items-center justify-between gap-3 py-1.5">
              <span className="text-[12.5px] text-text-secondary">{c.label}</span>
              <button onClick={() => revertChange(c.id)} className="text-[11.5px] font-semibold text-text-faint hover:text-danger cursor-pointer shrink-0">
                Revert
              </button>
            </div>
          ))}
        </div>
      )}

      <Modal open={confirmDiscard} onClose={() => setConfirmDiscard(false)} title="Discard unsaved changes" width={420}>
        <div className="flex flex-col gap-4">
          <p className="text-[13px] text-text-secondary">
            Discard all {changes.length} unsaved change{changes.length === 1 ? '' : 's'}? This can't be undone.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirmDiscard(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={discard}>
              Discard
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

// -----------------------------------------------------------------------
// Shell
// -----------------------------------------------------------------------
export function ConfigPage() {
  return (
    <WorkingSetProvider>
      <ConfigShell />
    </WorkingSetProvider>
  );
}

function ConfigShell() {
  const { changes } = useWorkingSet();
  // Deep link from elsewhere (e.g. Risk & Controls' unlisted-jurisdiction flag) - #jurisdiction.
  const [section, setSection] = useState<SectionId>(() => {
    const hash = window.location.hash.slice(1);
    return SECTIONS.some((s) => s.id === hash) ? (hash as SectionId) : 'categories';
  });
  const [scoringVersions, setScoringVersions] = useState<ScoringModelVersion[] | null>(null);

  function reloadScoring() {
    getScoringModelVersions().then(setScoringVersions);
  }

  useEffect(() => {
    reloadScoring();
  }, []);

  const activeScoringModel = scoringVersions?.find((v) => v.status === 'active') ?? null;

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-[26px] font-bold text-text-primary">Config</h1>
      </div>

      <WorkingSetBanner onNavigateToSection={setSection} />

      <div className="flex gap-4">
        <div className="w-[220px] shrink-0 flex flex-col gap-1.5">
          {SECTIONS.map((s) => {
            const pending = changes.filter((c) => c.sectionId === s.id).length;
            return (
              <Card
                key={s.id}
                material="flat"
                className={`p-0 overflow-hidden ${section === s.id ? 'border-carbon-moss' : ''}`}
              >
                <button
                  onClick={() => setSection(s.id)}
                  className={
                    'w-full text-left px-3.5 py-2.5 flex items-center justify-between gap-2 text-[13.5px] font-medium transition-colors cursor-pointer ' +
                    (section === s.id ? 'bg-[#e1e6e4] text-[#14363b] font-semibold' : 'text-text-secondary hover:bg-surface-tertiary')
                  }
                >
                  <span>{s.label}</span>
                  {pending > 0 && (
                    <span className="text-[10.5px] font-bold text-white bg-carbon-moss rounded-full min-w-[18px] h-[18px] px-1 flex items-center justify-center shrink-0">{pending}</span>
                  )}
                </button>
              </Card>
            );
          })}
        </div>

        <div className="flex-1 min-w-0">
          <div className={section === 'categories' ? '' : 'hidden'}>
            <CategoriesFieldsSection />
          </div>
          <div className={section === 'classification' ? '' : 'hidden'}>
            <ClassificationRulesSection onNavigateToCategories={() => setSection('categories')} />
          </div>
          <div className={section === 'scoring' ? '' : 'hidden'}>
            <ScoringModelSection onNavigateToRouting={() => setSection('routing')} refreshToken={scoringVersions} onPublished={reloadScoring} />
          </div>
          <div className={section === 'controls' ? '' : 'hidden'}>
            <ControlInventorySection />
          </div>
          <div className={section === 'policy' ? '' : 'hidden'}>
            <PolicyCorpusSection />
          </div>
          <div className={section === 'routing' ? '' : 'hidden'}>
            <RulesRoutingSection activeModel={activeScoringModel} onNavigateToScoring={() => setSection('scoring')} onScoringPublished={reloadScoring} />
          </div>
          <div className={section === 'agents' ? '' : 'hidden'}>
            <AIAgentsSection />
          </div>
          <div className={section === 'jurisdiction' ? '' : 'hidden'}>
            <JurisdictionRiskSection />
          </div>
        </div>
      </div>
    </div>
  );
}
