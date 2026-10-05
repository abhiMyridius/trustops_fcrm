import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { runAssuranceQuery, getScoringModelVersions, getFieldSetVersions, parseAssuranceNaturalLanguageQuery } from '@/services/analystApi';
import type { AmlCategory, AssuranceQuery, AssuranceResultRow, Band, ScoringModelVersion } from '@/types/analyst';
import { ALL_AML_CATEGORIES } from '@/types/analyst';
import type { FieldSetVersion } from '@/types';
import { ANALYSTS } from '@/mocks/seed/users';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Select, Checkbox, Input, Textarea, Field } from '@/components/ui/Form';
import { Icon } from '@/components/ui/Icon';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { formatDate } from '@/utils/format';

const EXAMPLE_QUERY: AssuranceQuery = { minBand: 3, staleEvidence: true };

const ROUTE_OPTIONS = ['Delegated close by analyst', 'Committee review'];

function bandTone(band: Band | null): 'default' | 'success' | 'warning' | 'danger' {
  if (band === null) return 'default';
  if (band <= 1) return 'success';
  if (band === 2) return 'default';
  if (band === 3) return 'warning';
  return 'danger';
}

function csvCell(v: string): string {
  return `"${v.replace(/"/g, '""')}"`;
}

// One file per query run, not one per case (§B4). Every field a case's own entry can produce is
// serialized into it, so the export stands on its own as evidence - no need to reopen each case.
function downloadResults(rows: AssuranceResultRow[], query: AssuranceQuery) {
  const criteria = describeQuery(query);
  const lines = [
    `Assurance query export`,
    `Query conditions: ${criteria || 'none - all cases'}`,
    `Generated: ${new Date().toISOString()}`,
    '',
    [
      'Case ID', 'Title', 'Categories', 'Analyst', 'Submitted', 'Closed', 'Status', 'Composite Residual', 'Band', 'Route Taken',
      'Inherent & Residual by AML Category', 'Control CE (with evidence status)', 'Policy Clauses Cited',
      'Scoring Model Version', 'Field-Set Version', 'Matched Reasons',
    ].join(','),
    ...rows.map((r) =>
      [
        r.caseId,
        csvCell(r.title),
        csvCell(r.categories.join('; ')),
        csvCell(r.analyst ?? ''),
        r.submittedAt ? formatDate(r.submittedAt) : '',
        r.closedAt ? formatDate(r.closedAt) : '',
        r.status,
        r.compositeResidual ?? '',
        r.band ?? '',
        csvCell(r.route ?? ''),
        csvCell(r.categoryScores.map((cs) => `${cs.category}: inherent ${cs.inherent} / residual ${cs.residual}`).join('; ')),
        csvCell(r.controls.map((ctl) => `${ctl.name} (${ctl.ce.toFixed(1)}, evidence: ${ctl.evidenceStatus})`).join('; ')),
        csvCell(r.policyClausesCited.map((pc) => `${pc.sourceDoc} ${pc.section}`).join('; ')),
        r.scoringModelVersion ?? '',
        r.fieldSetVersion ?? '',
        csvCell(r.matchedReasons.join(' | ')),
      ].join(','),
    ),
  ];
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `assurance-query-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function describeQuery(q: AssuranceQuery): string {
  const parts: string[] = [];
  if (q.minBand != null) parts.push(`Band ${q.minBand}+`);
  if (q.category) parts.push(q.category);
  if (q.judgmentOnlyControls) parts.push('judgment-only controls');
  if (q.staleEvidence) parts.push('stale evidence');
  if (q.minJurisdictionRisk != null) parts.push(`jurisdiction risk ${q.minJurisdictionRisk}+`);
  if (q.analyst) parts.push(`analyst: ${q.analyst}`);
  if (q.minResidual != null) parts.push(`residual >= ${q.minResidual}`);
  if (q.maxResidual != null) parts.push(`residual <= ${q.maxResidual}`);
  if (q.dateFrom) parts.push(`from ${q.dateFrom}`);
  if (q.dateTo) parts.push(`to ${q.dateTo}`);
  if (q.policyClauseCited) parts.push(`cites "${q.policyClauseCited}"`);
  if (q.scoringModelVersion) parts.push(`scoring model ${q.scoringModelVersion}`);
  if (q.fieldSetVersion != null) parts.push(`field set v${q.fieldSetVersion}`);
  if (q.boundaryFlagFired) parts.push('boundary flag fired');
  if (q.routeTaken) parts.push(`route: ${q.routeTaken}`);
  return parts.join(', ');
}

export function AssurancePage() {
  const navigate = useNavigate();
  const [minBand, setMinBand] = useState<Band | ''>('');
  const [category, setCategory] = useState<AmlCategory | ''>('');
  const [judgmentOnlyControls, setJudgmentOnlyControls] = useState(false);
  const [staleEvidence, setStaleEvidence] = useState(false);
  const [minJurisdictionRisk, setMinJurisdictionRisk] = useState<number | ''>('');
  const [analyst, setAnalyst] = useState<string>('');
  const [minResidual, setMinResidual] = useState<number | ''>('');
  const [maxResidual, setMaxResidual] = useState<number | ''>('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [policyClauseCited, setPolicyClauseCited] = useState('');
  const [scoringModelVersion, setScoringModelVersion] = useState('');
  const [fieldSetVersion, setFieldSetVersion] = useState<number | ''>('');
  const [boundaryFlagFired, setBoundaryFlagFired] = useState(false);
  const [routeTaken, setRouteTaken] = useState('');

  const [scoringVersions, setScoringVersions] = useState<ScoringModelVersion[]>([]);
  const [fieldSetVersions, setFieldSetVersions] = useState<FieldSetVersion[]>([]);

  const [results, setResults] = useState<AssuranceResultRow[] | null>(null);
  const [ranQuery, setRanQuery] = useState<AssuranceQuery | null>(null);
  const [loading, setLoading] = useState(false);

  const [nlQuery, setNlQuery] = useState('');
  const [nlParsing, setNlParsing] = useState(false);
  const [nlMatched, setNlMatched] = useState<string[] | null>(null);
  const [nlUnparsed, setNlUnparsed] = useState(false);

  useEffect(() => {
    getScoringModelVersions().then(setScoringVersions);
    getFieldSetVersions().then(setFieldSetVersions);
  }, []);

  function currentQuery(): AssuranceQuery {
    return {
      minBand: minBand === '' ? undefined : minBand,
      category: category === '' ? undefined : category,
      judgmentOnlyControls: judgmentOnlyControls || undefined,
      staleEvidence: staleEvidence || undefined,
      minJurisdictionRisk: minJurisdictionRisk === '' ? undefined : minJurisdictionRisk,
      analyst: analyst || undefined,
      minResidual: minResidual === '' ? undefined : minResidual,
      maxResidual: maxResidual === '' ? undefined : maxResidual,
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
      policyClauseCited: policyClauseCited.trim() || undefined,
      scoringModelVersion: scoringModelVersion || undefined,
      fieldSetVersion: fieldSetVersion === '' ? undefined : fieldSetVersion,
      boundaryFlagFired: boundaryFlagFired || undefined,
      routeTaken: routeTaken || undefined,
    };
  }

  async function runQuery(query: AssuranceQuery) {
    setLoading(true);
    const rows = await runAssuranceQuery(query);
    setResults(rows);
    setRanQuery(query);
    setLoading(false);
  }

  function resetFilters() {
    setMinBand('');
    setCategory('');
    setJudgmentOnlyControls(false);
    setStaleEvidence(false);
    setMinJurisdictionRisk('');
    setAnalyst('');
    setMinResidual('');
    setMaxResidual('');
    setDateFrom('');
    setDateTo('');
    setPolicyClauseCited('');
    setScoringModelVersion('');
    setFieldSetVersion('');
    setBoundaryFlagFired(false);
    setRouteTaken('');
  }

  function loadExample() {
    resetFilters();
    setMinBand(EXAMPLE_QUERY.minBand ?? '');
    setStaleEvidence(!!EXAMPLE_QUERY.staleEvidence);
    runQuery(EXAMPLE_QUERY);
  }

  // Applies what the NL parser recognized onto the structured filters - it only populates them,
  // it never runs a query itself. The analyst reviews, corrects, and runs explicitly (§B1).
  function applyParsedQuery(q: Partial<AssuranceQuery>) {
    resetFilters();
    if (q.minBand != null) setMinBand(q.minBand);
    if (q.category) setCategory(q.category);
    if (q.judgmentOnlyControls) setJudgmentOnlyControls(true);
    if (q.staleEvidence) setStaleEvidence(true);
    if (q.minJurisdictionRisk != null) setMinJurisdictionRisk(q.minJurisdictionRisk);
    if (q.analyst) setAnalyst(q.analyst);
    if (q.minResidual != null) setMinResidual(q.minResidual);
    if (q.maxResidual != null) setMaxResidual(q.maxResidual);
    if (q.dateFrom) setDateFrom(q.dateFrom);
    if (q.dateTo) setDateTo(q.dateTo);
    if (q.policyClauseCited) setPolicyClauseCited(q.policyClauseCited);
    if (q.scoringModelVersion) setScoringModelVersion(q.scoringModelVersion);
    if (q.fieldSetVersion != null) setFieldSetVersion(q.fieldSetVersion);
    if (q.boundaryFlagFired) setBoundaryFlagFired(true);
    if (q.routeTaken) setRouteTaken(q.routeTaken);
  }

  async function handleInterpret() {
    if (!nlQuery.trim()) return;
    setNlParsing(true);
    setNlUnparsed(false);
    setNlMatched(null);
    const parsed = await parseAssuranceNaturalLanguageQuery(nlQuery);
    setNlParsing(false);
    if (!parsed) {
      setNlUnparsed(true);
      return;
    }
    applyParsedQuery(parsed.query);
    setNlMatched(parsed.matchedPhrases);
  }

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-[26px] font-bold text-text-primary">Assurance</h1>
        <p className="text-[13.5px] text-text-muted mt-1">Query across every case's scoring, controls, and jurisdiction exposure across all analysts' cases.</p>
      </div>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <span className="text-sm font-bold text-text-primary flex items-center gap-2">
          <Icon name="sparkles" size={15} className="text-teal" />
          Ask in plain language
        </span>
        <p className="text-[12.5px] text-text-muted -mt-1">
          Describe what you're looking for, e.g. "vendor cases in 2026 above Band 3 with stale control evidence." This only fills in the filters below - nothing runs until you do.
        </p>
        <div className="flex gap-2 items-start">
          <Textarea rows={2} value={nlQuery} onChange={(e) => setNlQuery(e.target.value)} placeholder="Describe the cases you're looking for..." className="flex-1" />
          <Button variant="primary" loading={nlParsing} disabled={!nlQuery.trim()} onClick={handleInterpret}>
            Interpret
          </Button>
        </div>
        {nlMatched && (
          <div className="flex items-start gap-2 bg-[#F4F8F7] rounded-md px-3 py-2.5">
            <Icon name="check" size={14} className="text-teal shrink-0 mt-0.5" />
            <p className="text-[12.5px] text-[#133338] leading-snug">
              Interpreted as: <strong>{nlMatched.join(', ')}</strong>. Review the filters below, correct anything, then run the query.
            </p>
          </div>
        )}
        {nlUnparsed && (
          <div className="flex items-start gap-2 bg-danger-bg rounded-md px-3 py-2.5">
            <Icon name="warning" size={14} className="text-danger shrink-0 mt-0.5" />
            <p className="text-[12.5px] text-danger leading-snug">Couldn't interpret that as a set of criteria - try rephrasing, or set the filters directly below. Nothing was changed.</p>
          </div>
        )}
      </Card>

      <Card material="flat" className="p-5 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-text-primary">Query criteria</span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="secondary" onClick={resetFilters}>
              Reset
            </Button>
            <Button size="sm" variant="secondary" icon={<Icon name="sparkles" size={14} />} onClick={loadExample}>
              Load example query
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-4 gap-4">
          <Field label="Minimum band">
            <Select value={minBand} onChange={(e) => setMinBand(e.target.value === '' ? '' : (Number(e.target.value) as Band))}>
              <option value="">Any band</option>
              <option value="1">Band 1</option>
              <option value="2">Band 2</option>
              <option value="3">Band 3</option>
              <option value="4">Band 4</option>
            </Select>
          </Field>
          <Field label="Category in scope">
            <Select value={category} onChange={(e) => setCategory(e.target.value as AmlCategory | '')}>
              <option value="">Any category</option>
              {ALL_AML_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Minimum jurisdiction risk">
            <Select value={minJurisdictionRisk} onChange={(e) => setMinJurisdictionRisk(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">Any</option>
              <option value="3">3+</option>
              <option value="4">4+</option>
              <option value="5">5</option>
            </Select>
          </Field>
          <Field label="Analyst">
            <Select value={analyst} onChange={(e) => setAnalyst(e.target.value)}>
              <option value="">Any analyst</option>
              {ANALYSTS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Composite residual, min">
            <Input type="number" min={0} max={25} step={0.1} placeholder="0.0" value={minResidual} onChange={(e) => setMinResidual(e.target.value === '' ? '' : Number(e.target.value))} />
          </Field>
          <Field label="Composite residual, max">
            <Input type="number" min={0} max={25} step={0.1} placeholder="25.0" value={maxResidual} onChange={(e) => setMaxResidual(e.target.value === '' ? '' : Number(e.target.value))} />
          </Field>
          <Field label="Submitted from">
            <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </Field>
          <Field label="Submitted to">
            <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </Field>

          <Field label="Policy clause cited">
            <Input type="text" placeholder="Text in clause or source doc" value={policyClauseCited} onChange={(e) => setPolicyClauseCited(e.target.value)} />
          </Field>
          <Field label="Scoring model version">
            <Select value={scoringModelVersion} onChange={(e) => setScoringModelVersion(e.target.value)}>
              <option value="">Any version</option>
              {scoringVersions.map((v) => (
                <option key={v.version} value={v.version}>
                  {v.version} {v.status === 'active' ? '(active)' : ''}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Field-set version">
            <Select value={fieldSetVersion} onChange={(e) => setFieldSetVersion(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">Any version</option>
              {fieldSetVersions.map((v) => (
                <option key={v.version} value={v.version}>
                  v{v.version}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Route taken">
            <Select value={routeTaken} onChange={(e) => setRouteTaken(e.target.value)}>
              <option value="">Any route</option>
              {ROUTE_OPTIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <div className="flex items-center gap-6 flex-wrap">
          <Checkbox checked={judgmentOnlyControls} onChange={setJudgmentOnlyControls} label="Has a judgment-only control (no documentary evidence)" />
          <Checkbox checked={staleEvidence} onChange={setStaleEvidence} label="Has a control with stale evidence" />
          <Checkbox checked={boundaryFlagFired} onChange={setBoundaryFlagFired} label="Boundary proximity flag fired" />
        </div>

        <div className="flex justify-end">
          <Button variant="primary" onClick={() => runQuery(currentQuery())} loading={loading}>
            Run query
          </Button>
        </div>
      </Card>

      {loading && <Skeleton className="h-64 rounded-md" />}

      {!loading && results && (
        <Card material="flat" className="overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-border bg-[#F4F8F7] flex-wrap gap-3">
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-bold text-text-primary">{results.length} matching case{results.length === 1 ? '' : 's'}</span>
              <span className="text-[12px] text-text-faint">{describeQuery(ranQuery!) || 'No filters applied'}</span>
            </div>
            <Button variant="primary" icon={<Icon name="download" size={15} />} onClick={() => downloadResults(results, ranQuery!)} disabled={results.length === 0}>
              Export with reasoning
            </Button>
          </div>
          {results.length === 0 ? (
            <div className="p-8">
              <EmptyState icon="search" title="No cases match" description="Widen the criteria - try a lower band threshold or removing a checkbox filter." />
            </div>
          ) : (
            <div className="flex flex-col divide-y divide-border-hairline">
              {results.map((r) => (
                <button key={r.caseId} onClick={() => navigate(`/analyst/case/${r.caseId}`)} className="w-full text-left px-5 py-4 hover:bg-surface-tertiary cursor-pointer flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <span className="text-[12px] font-mono text-text-faint shrink-0">{r.caseId}</span>
                      <span className="text-[13.5px] font-semibold text-text-primary truncate">{r.title}</span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {r.band && <Badge tone={bandTone(r.band)}>Band {r.band}{r.compositeResidual != null ? ` · ${r.compositeResidual}` : ''}</Badge>}
                      <Badge tone="default">{r.status}</Badge>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-[12px] text-text-muted">
                    <span className="flex items-center gap-1">
                      <Icon name="user" size={12} className="text-text-faint" />
                      {r.analyst ?? 'Unassigned'}
                    </span>
                    <span className="flex items-center gap-1">
                      <Icon name="calendar" size={12} className="text-text-faint" />
                      {r.submittedAt ? formatDate(r.submittedAt) : 'Not submitted'}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {r.matchedReasons.map((reason, i) => (
                      <span key={i} className="text-[11.5px] text-teal bg-verdant-1 px-2 py-1 rounded flex items-center gap-1">
                        <Icon name="check" size={11} />
                        {reason}
                      </span>
                    ))}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Card>
      )}

      {!loading && !results && (
        <EmptyState icon="search" title="Set your criteria and run a query" />
      )}
    </div>
  );
}
