/**
 * The mock backend's policy corpus: the four clauses the seed carries for FCRM-2026-0151 plus one
 * synthetic clause research did not propose, so a corpus search can return a hit the analyst may add
 * to the case (ADR-74). The extra clause is mock data, labelled as such; the live corpus is S3's.
 */
import { seed } from './seed';

export interface MockCorpusClause {
  clause_id: string;
  src: string;
  title: string;
  quote: string;
  ref: string;
  quotable: boolean;
}

/** Clauses only the mock corpus holds (never proposed by research in the seed). */
export const MOCK_EXTRA_CLAUSES: MockCorpusClause[] = [
  {
    clause_id: 'MOCK-AGT-01',
    src: 'MOCK',
    title: 'Agent network oversight (mock corpus)',
    quote:
      'Synthetic text for the mock backend: a payment institution that pays out through agents should know how each agent is selected, trained and monitored.',
    ref: 'Mock corpus · synthetic · p.1',
    quotable: true,
  },
];

/** Every clause the mock corpus holds, the seed's first. */
export function mockCorpus(): MockCorpusClause[] {
  return [...seed.demo.clauses.map((c) => ({ clause_id: c.clause_id, src: c.src, title: c.title, quote: c.quote, ref: c.ref, quotable: true })), ...MOCK_EXTRA_CLAUSES];
}

/** One clause of the mock corpus, or `undefined`. */
export function corpusClause(clauseId: string): MockCorpusClause | undefined {
  return mockCorpus().find((c) => c.clause_id === clauseId);
}
