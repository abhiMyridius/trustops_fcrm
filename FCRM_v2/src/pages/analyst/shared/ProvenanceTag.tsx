import type { ValueProvenance } from '@/types/analyst';

const LABEL: Record<ValueProvenance, string> = {
  submission: 'Submission',
  'jurisdiction-list': 'Jurisdiction risk list',
  'jurisdiction-list-override': 'Jurisdiction risk list, adjusted by analyst',
  'control-inventory': 'Control inventory',
  'analyst-override': 'Analyst override',
};

const COLOR: Record<ValueProvenance, string> = {
  submission: 'text-digital-pavo bg-[#EAF3F3]',
  'jurisdiction-list': 'text-teal bg-verdant-1',
  'jurisdiction-list-override': 'text-warning bg-[#FBF3E4]',
  'control-inventory': 'text-carbon-moss bg-surface-tertiary',
  'analyst-override': 'text-warning bg-[#FBF3E4]',
};

/** Provenance is always a visible label, never a tooltip (§6.5 rule). */
export function ProvenanceTag({ source }: { source: ValueProvenance }) {
  return <span className={`inline-block text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded ${COLOR[source]}`}>{LABEL[source]}</span>;
}
