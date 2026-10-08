/**
 * The mock backend's state. FCRM-2026-0151 is a live state machine whose actions append the seed's
 * audit records in story order, so a full click-through ends at exactly 67 records; the other 79
 * seeded cases are read-only projections of seed.json. Time-based steps (document reading, the
 * assessment core) advance lazily on each request (`tick`) so the harness monitor fills up live.
 * State survives page reloads through sessionStorage (browser only); a scenario switch resets it.
 */
import { seed, DEMO_ID, type SeedAuditRecord } from './seed';

export type ScenarioKey = 'fresh' | 'submitted' | 'released' | 'closed';

export const SCENARIOS: { key: ScenarioKey; label: string; scene: string }[] = [
  { key: 'fresh', label: 'Scene 1 · case seeded at intake, no documents yet', scene: 'S1' },
  { key: 'submitted', label: 'Scene 8 · submitted, assessment core done, analyst to review', scene: 'S8' },
  { key: 'released', label: 'Scene 17 · pack released, 1 of 3 votes cast', scene: 'S17' },
  { key: 'closed', label: 'Scene 19 · decided 2–1, closed, 67 records', scene: 'S19' },
];

export interface AuditEntry {
  seq: number;
  n: number | null;
  type: string;
  by: string;
  what: string;
  at: string;
  scene: number;
  hash: string;
  prev_hash: string;
}

export interface DocEntry {
  id: string;
  name: string;
  pages: number;
  size_mb: number;
  uploaded_at: string;
  added_in: string | null;
}

export interface VoteEntry {
  choice: 'approve' | 'approve_with_conditions' | 'defer' | 'reject';
  rationale: string;
  at: string;
}

export interface CommitteeThreadEntry {
  id: string;
  from: string;
  question: string;
  at: string;
  answer: { by: string; text: string; at: string; addendum_no: number; numbers_changed: boolean } | null;
}

export interface PlannedEntry {
  id: string;
  control_id: string | null;
  description: string;
  owner: string;
  due: string;
}

export interface AnalystClauseEntry {
  reason: string;
  by: string;
  removed: boolean;
  removeReason: string | null;
}

export interface DemoState {
  docs: DocEntry[];
  readingStartedAt: number | null;
  intakeReady: boolean;
  /** `origin: 'owner'` marks a type the owner added herself (ADR-72); the rest are the assistant's proposals. */
  types: Record<string, { status: 'proposed' | 'confirmed' | 'removed'; reason: string | null; origin?: 'owner' }>;
  reused: boolean;
  gapsAnswered: boolean;
  corrected: boolean;
  submitted: boolean;
  assessStartedAt: number | null;
  completeness: 'ask' | 'proceed' | null;
  roundSent: boolean;
  threadAnswers: Record<string, { text: string; documents: string[]; at: string }>;
  resolved: string[];
  clauses: Record<string, { decision: 'pending' | 'accepted' | 'removed'; reason: string | null }>;
  /** ADR-74: clauses the analyst added from a corpus search, by clause id (absent in an older saved state). */
  analystClauses?: Record<string, AnalystClauseEntry>;
  gapRecorded: boolean;
  controls: Record<string, { decision: 'pending' | 'confirmed' | 'overridden'; rating: number; reason: string | null }>;
  planned: PlannedEntry[];
  acknowledged: boolean;
  narrative: string;
  outcomeChoice: 'approve' | 'approve_with_conditions' | 'defer' | 'reject';
  businessSummary: string;
  edits: number;
  locked: boolean;
  packBuilt: boolean;
  packReleasedAt: string | null;
  packVersion: number;
  committeeThreads: CommitteeThreadEntry[];
  votes: Record<string, VoteEntry>;
  voteRound: number;
  outcome: { outcome: 'approve' | 'approve_with_conditions' | 'defer' | 'reject'; at: string; mode: 'committee' | 'delegated' } | null;
  conditionEvidence: Record<string, string>;
  closed: boolean;
  audit: AuditEntry[];
}

export interface CreatedCase {
  id: string;
  title: string;
  unit: string;
  owner: string;
  created: string;
  docs: DocEntry[];
  readingStartedAt: number | null;
  discarded: boolean;
  /** ADR-72: the path chosen on the New case form; optional so a stored session from before still loads. */
  mode?: 'ai_assisted' | 'manual';
  /** The owner's own change types (the manual path, or added on the case). */
  types?: string[];
  /** Values the owner typed by hand, by field id. */
  values?: Record<string, string>;
}

export interface ConfigState {
  extraVersions: { version: string; published_at: string; by: string; note: string }[];
  draft: { path: string; before: string; after: string }[];
  etag: number;
}

export interface DB {
  version: number;
  scenario: ScenarioKey;
  demo: DemoState;
  created: CreatedCase[];
  nextCaseNo: number;
  fastLane: Record<string, boolean>;
  reflagAck: Record<string, boolean>;
  integrations: Record<string, { status: 'connected' | 'connected_demo' | 'not_connected'; scope: string | null }>;
  otherVotes: Record<string, Record<string, VoteEntry>>;
  otherThreads: Record<string, CommitteeThreadEntry[]>;
  config: Record<string, ConfigState>;
}

const DB_VERSION = 3;
const STORAGE_KEY = 'trustops.mock.db';
export const SCENARIO_KEY = 'trustops.mock.scenario';

/** Story clock: the seed's scenes happen 1–3 Oct 2026 (walkthrough v2). */
const SCENE_TIME: Record<number, string> = {
  1: '2026-10-01T08:55:00Z',
  2: '2026-10-01T09:02:00Z',
  3: '2026-10-01T09:10:00Z',
  4: '2026-10-01T09:20:00Z',
  5: '2026-10-01T09:24:00Z',
  6: '2026-10-01T09:30:00Z',
  7: '2026-10-01T09:40:00Z',
  8: '2026-10-01T09:45:00Z',
  9: '2026-10-01T10:05:00Z',
  10: '2026-10-01T10:30:00Z',
  11: '2026-10-01T14:05:00Z',
  12: '2026-10-02T09:10:00Z',
  13: '2026-10-02T09:40:00Z',
  14: '2026-10-02T10:05:00Z',
  15: '2026-10-02T11:00:00Z',
  16: '2026-10-02T11:30:00Z',
  17: '2026-10-02T15:00:00Z',
  18: '2026-10-03T10:00:00Z',
  19: '2026-10-03T10:15:00Z',
};

export function sceneTime(scene: number, offsetMinutes = 0): string {
  const base = SCENE_TIME[scene] ?? SCENE_TIME[19]!;
  return new Date(new Date(base).getTime() + offsetMinutes * 60_000).toISOString();
}

/** "Today" for due-date and clock displays. */
export const STORY_TODAY = '2026-10-03';

/** Deterministic 64-hex digest (FNV-1a lanes). Mock only: the real chain is sha256 in S4. */
export function fakeHash(input: string): string {
  let out = '';
  for (let lane = 0; lane < 8; lane++) {
    let h = 0x811c9dc5 ^ (lane * 0x9e3779b1);
    for (let i = 0; i < input.length; i++) {
      h ^= input.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    out += (h >>> 0).toString(16).padStart(8, '0');
  }
  return out;
}

const AUDIT_BY_N = new Map<number, SeedAuditRecord>(seed.demo.audit.map((r) => [r.n, r]));

export function seedRecord(n: number): SeedAuditRecord {
  const r = AUDIT_BY_N.get(n);
  if (!r) throw new Error(`seed audit record ${n} missing`);
  return r;
}

function initialDemo(): DemoState {
  const types: DemoState['types'] = {};
  for (const t of seed.demo.change_types) types[t] = { status: 'proposed', reason: null };
  const clauses: DemoState['clauses'] = {};
  for (const c of seed.demo.clauses) clauses[c.clause_id] = { decision: 'pending', reason: null };
  const controls: DemoState['controls'] = {};
  for (const area of Object.values(seed.demo.score_before.areas)) {
    for (const c of area?.controls ?? []) {
      if (c.rating !== null && !c.planned) controls[c.id] = { decision: 'pending', rating: c.rating, reason: null };
    }
  }
  return {
    docs: [],
    readingStartedAt: null,
    intakeReady: false,
    types,
    reused: false,
    gapsAnswered: false,
    corrected: false,
    submitted: false,
    assessStartedAt: null,
    completeness: null,
    roundSent: false,
    threadAnswers: {},
    resolved: [],
    clauses,
    gapRecorded: false,
    controls,
    planned: [],
    acknowledged: false,
    narrative: seed.demo.recommendation.text,
    outcomeChoice: 'approve_with_conditions',
    businessSummary:
      'Your change can go ahead once three conditions are met: a corridor-specific review of Remesas del Norte is completed and filed before go-live; enhanced monitoring of payout agents goes live with the launch; and a quarterly sample review of agent customer files runs for the first six months.',
    edits: 0,
    locked: false,
    packBuilt: false,
    packReleasedAt: null,
    packVersion: 0,
    committeeThreads: [],
    votes: {},
    voteRound: 1,
    outcome: null,
    conditionEvidence: {},
    closed: false,
    audit: [],
  };
}

function initialDB(scenario: ScenarioKey): DB {
  return {
    version: DB_VERSION,
    scenario,
    demo: initialDemo(),
    created: [],
    nextCaseNo: 152,
    fastLane: {},
    reflagAck: {},
    integrations: {
      sharepoint: { status: 'connected_demo', scope: 'Payments / Change requests' },
      teams: { status: 'not_connected', scope: null },
      slack: { status: 'not_connected', scope: null },
      catalogue: { status: 'not_connected', scope: null },
    },
    otherVotes: {},
    otherThreads: {},
    config: {},
  };
}

let db: DB = initialDB('fresh');

export function getDB(): DB {
  return db;
}

// ---------------------------------------------------------------- audit chain

/** Append a seed record by its number (idempotent: a story record is written once). */
export function appendSeed(n: number): AuditEntry | null {
  const d = db.demo;
  if (d.audit.some((a) => a.n === n)) return null;
  const r = seedRecord(n);
  return push({ n, type: r.type, by: r.by, what: r.what, scene: r.scene });
}

/** Append a record the seed's story does not contain (an alternative path the analyst took). */
export function appendExtra(type: string, by: string, what: string): AuditEntry {
  const last = db.demo.audit[db.demo.audit.length - 1];
  return push({ n: null, type, by, what, scene: last?.scene ?? 2 });
}

function push(r: { n: number | null; type: string; by: string; what: string; scene: number }): AuditEntry {
  const d = db.demo;
  const prev = d.audit[d.audit.length - 1];
  const seq = d.audit.length + 1;
  const prevHash = prev?.hash ?? fakeHash('genesis:' + DEMO_ID);
  const at = sceneTime(r.scene, seq % 50);
  const entry: AuditEntry = { seq, ...r, at, prev_hash: prevHash, hash: fakeHash(`${prevHash}\n${seq}|${r.type}|${r.by}|${r.what}`) };
  d.audit.push(entry);
  return entry;
}

export function hasRecord(n: number): boolean {
  return db.demo.audit.some((a) => a.n === n);
}

// ---------------------------------------------------------------- time-based progression

export const READ_MS = 3500;
export const ASSESS_STEP_MS = 450;
const ASSESS_RECORDS = [25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35];
const INTAKE_RECORDS = [3, 4, 5, 6, 7, 8, 9, 10];

/** Advance time-based steps. Called at the top of every handler. */
export function tick(now = Date.now()): void {
  const d = db.demo;
  if (d.readingStartedAt !== null && !d.intakeReady) {
    const elapsed = now - d.readingStartedAt;
    const due = Math.min(INTAKE_RECORDS.length, Math.floor((elapsed / READ_MS) * INTAKE_RECORDS.length));
    INTAKE_RECORDS.slice(0, due).forEach((n) => appendSeed(n));
    if (elapsed >= READ_MS) {
      INTAKE_RECORDS.forEach((n) => appendSeed(n));
      d.intakeReady = true;
    }
  }
  if (d.assessStartedAt !== null) {
    const due = Math.min(ASSESS_RECORDS.length, Math.floor((now - d.assessStartedAt) / ASSESS_STEP_MS) + 1);
    ASSESS_RECORDS.slice(0, due).forEach((n) => appendSeed(n));
    if (due >= ASSESS_RECORDS.length) d.assessStartedAt = null;
  }
  for (const c of db.created) {
    if (c.readingStartedAt !== null && now - c.readingStartedAt >= READ_MS) c.readingStartedAt = null;
  }
}

export function assessmentDone(): boolean {
  return db.demo.submitted && db.demo.assessStartedAt === null && hasRecord(35);
}

// ---------------------------------------------------------------- persistence + scenarios

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

export function persist(): void {
  try {
    storage()?.setItem(STORAGE_KEY, JSON.stringify(db));
  } catch {
    // Per-viewer convenience only; the mock still works without storage.
  }
}

export function readScenarioPreference(): ScenarioKey {
  try {
    const v = typeof window !== 'undefined' ? window.localStorage.getItem(SCENARIO_KEY) : null;
    return SCENARIOS.some((s) => s.key === v) ? (v as ScenarioKey) : 'fresh';
  } catch {
    return 'fresh';
  }
}

export function writeScenarioPreference(key: ScenarioKey): void {
  try {
    window.localStorage.setItem(SCENARIO_KEY, key);
  } catch {
    // ignore
  }
}

/** Boot: restore the session's DB if it matches the preferred scenario, else build the scenario fresh. */
export function boot(apply: (key: ScenarioKey) => void): void {
  const preferred = readScenarioPreference();
  try {
    const raw = storage()?.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as DB;
      if (parsed.version === DB_VERSION && parsed.scenario === preferred) {
        db = parsed;
        return;
      }
    }
  } catch {
    // fall through to a fresh build
  }
  resetTo(preferred, apply);
}

export function resetTo(key: ScenarioKey, apply: (key: ScenarioKey) => void): void {
  db = initialDB(key);
  apply(key);
  persist();
}

/** Tests start from a known scenario without storage. */
export function resetForTests(key: ScenarioKey, apply: (key: ScenarioKey) => void): void {
  db = initialDB(key);
  apply(key);
}
