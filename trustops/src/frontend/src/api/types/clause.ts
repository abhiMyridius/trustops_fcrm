/**
 * S3 `GET /policy/clauses/{clause_id}` (ClauseDetailOut): one policy clause as the system of record
 * holds it. `text` is verbatim, never generated. Analyst and committee only; the PO fence
 * (ADR-32) keeps this file out of `features/po/**`.
 */
export interface ClauseDetail {
  clause_id: string;
  source_id: string;
  title: string;
  text: string;
  ref: string;
  corpus_version: number;
  page: number | null;
  line_start: number | null;
  line_end: number | null;
  quotable: boolean;
  provenance_kind: string | null;
}
