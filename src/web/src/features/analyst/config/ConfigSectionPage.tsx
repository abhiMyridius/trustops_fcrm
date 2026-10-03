import { useParams } from 'react-router-dom';
import { useConfigSection } from '@/api/queries/configQueries';
import type { ConfigSection, ConfigSectionKey } from '@/api/types/config';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { useDocumentTitle } from '@/lib/a11y';
import { SECTIONS } from './ConfigLayout';
import { VersionHistory } from './VersionHistory';
import { WorkingSetBanner } from './WorkingSetBanner';
import { AIAgents } from './sections/AIAgents';
import { Categories } from './sections/Categories';
import { ControlInventory } from './sections/ControlInventory';
import { FieldSets } from './sections/FieldSets';
import { JurisdictionRisk } from './sections/JurisdictionRisk';
import { PolicyCorpus } from './sections/PolicyCorpus';
import { RoutingRules } from './sections/RoutingRules';
import { ScoringModel } from './sections/ScoringModel';

function Body({ s }: { s: ConfigSection }) {
  switch (s.section) {
    case 'field-sets':
      return <FieldSets s={s} />;
    case 'categories':
      return <Categories s={s} />;
    case 'scoring-model':
      return <ScoringModel s={s} />;
    case 'control-inventory':
      return <ControlInventory s={s} />;
    case 'policy-corpus':
      return <PolicyCorpus s={s} />;
    case 'routing-rules':
      return <RoutingRules s={s} />;
    case 'jurisdiction-risk':
      return <JurisdictionRisk s={s} />;
    case 'ai-agents':
      return <AIAgents s={s} />;
  }
}

function Section({ section }: { section: ConfigSectionKey }) {
  const q = useConfigSection(section);
  return (
    <QueryState query={q} label="Loading section">
      {(s) => (
        <div className="flex flex-col gap-5">
          <div>
            <h2 className="text-lg font-bold">{s.title}</h2>
            <p className="text-[13px] text-text-secondary">
              Active {s.active_version} · owner {s.owner}
            </p>
          </div>
          <WorkingSetBanner section={s} />
          <Body s={s} />
          <VersionHistory versions={s.versions} />
        </div>
      )}
    </QueryState>
  );
}

export function ConfigSectionPage() {
  const { section = '' } = useParams();
  const meta = SECTIONS.find((s) => s.key === section);
  useDocumentTitle(meta?.label, 'Config');
  if (!meta) return <EmptyState title="No such configuration section" />;
  return <Section key={section} section={section as ConfigSectionKey} />;
}
