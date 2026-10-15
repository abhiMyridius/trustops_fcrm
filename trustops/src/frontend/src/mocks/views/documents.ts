/**
 * The source preview's mock backend (ADR-76): a document's pages as H3 would have anchored them
 * (`p.N l.M` lines), with the seed's cited values placed at their cited lines, and a tiny PDF for
 * the file route. The real services read the anchored Markdown and the upload from MinIO.
 */
import type { SourceRef } from '@/api/types/common';
import type { DocumentPagePO } from '@/api/types/po';
import { getDB, type DocEntry } from '../db';
import { seed } from '../seed';
import { createdCase, isDemo } from './common';

/** The short names the seed's citations use for the demo documents. */
const DOC_ALIAS: Record<string, string> = {
  case: 'business_case_remittance_MX.pdf',
  contract: 'payout_partner_contract.pdf',
  letter: 'vendor_DD_scope_letter_2026.pdf',
};

const LINES_PER_PAGE = 12;

/** Neutral filler for the lines nothing cites (no word the owner's text guard would stop on). */
const FILLER = [
  'The corridor serves consumers sending money from the United States to family in Mexico.',
  'Payouts are made through the partner network at bank branches and partner agents.',
  'The partner settles funds through its own accounts in Mexico on the next business day.',
  'Customer identification follows the bank programme already in force for retail accounts.',
  'Transaction monitoring runs on the existing platform with corridor-specific thresholds.',
  'Screening of senders and receivers happens before funds are released to the partner.',
  'The partner shares agent lists and audit reports on a quarterly schedule.',
  'Marketing targets existing retail customers through the mobile app and the web.',
  'Service levels are set in the contract with monthly reporting to the bank.',
  'Complaints are handled by the bank, with the partner providing transaction detail.',
  'Volumes are forecast from the current customer base and comparable corridors.',
  'The programme will be reviewed after the first full quarter of operation.',
];

export function docsOf(caseId: string): DocEntry[] {
  if (isDemo(caseId)) return getDB().demo.docs;
  return createdCase(caseId)?.docs ?? [];
}

/**
 * "case p.1 l.9" → a SourceRef that names the uploaded document, so the chip opens the preview;
 * any other label (an owner's answer, a reused value, a document not uploaded yet) stays a label.
 */
export function srcRef(label: string | undefined | null, caseId: string = seed.demo.id): SourceRef | null {
  if (!label) return null;
  const m = /^(case|contract|letter) p\.(\d+)(?: l\.(\d+))?/.exec(label);
  if (!m) return { label };
  const name = DOC_ALIAS[m[1] ?? ''];
  const doc = docsOf(caseId).find((d) => d.name === name);
  if (!doc) return { label };
  const page = Number(m[2]);
  return m[3] ? { label, document: doc.name, doc_id: doc.id, page, line: Number(m[3]) } : { label, document: doc.name, doc_id: doc.id, page };
}

/** `"page:line"` → the seed value cited there, for one document. */
function citedLines(name: string): Map<string, string> {
  const alias = Object.entries(DOC_ALIAS).find(([, n]) => n === name)?.[0];
  const out = new Map<string, string>();
  if (!alias) return out;
  const re = new RegExp(`^${alias} p\\.(\\d+) l\\.(\\d+)$`);
  for (const f of seed.demo.fields) {
    const m = f.source ? re.exec(f.source) : null;
    if (m && f.value) out.set(`${m[1]}:${m[2]}`, f.value);
  }
  return out;
}

/** One page of a document, the cited span marked and, with `context`, trimmed around it; null when there is no such document or page. */
export function documentPage(
  caseId: string,
  docId: string,
  page: number,
  line?: number,
  lineEnd?: number,
  context?: number,
): DocumentPagePO | null {
  const doc = docsOf(caseId).find((d) => d.id === docId);
  if (!doc || !Number.isInteger(page) || page < 1 || page > doc.pages) return null;
  const cited = citedLines(doc.name);
  let lines = Array.from({ length: LINES_PER_PAGE }, (_, i) => {
    const n = i + 1;
    return { line: n, text: cited.get(`${page}:${n}`) ?? (FILLER[(page * 7 + n) % FILLER.length] as string) };
  });
  const highlight = line ? { line_start: line, line_end: lineEnd ?? line } : null;
  if (highlight && context !== undefined) {
    lines = lines.filter((l) => l.line >= highlight.line_start - context && l.line <= highlight.line_end + context);
  }
  return { doc_id: doc.id, name: doc.name, page, pages: doc.pages, lines, highlight };
}

/** A one-page PDF, enough for the browser to show something at `#page=1`. */
export function documentFile(): string {
  return [
    '%PDF-1.4',
    '1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj',
    '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj',
    '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj',
    'trailer<</Root 1 0 R>>',
    '%%EOF',
  ].join('\n');
}
