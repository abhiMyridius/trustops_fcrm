/** Starts each sweep run from an empty slate: the report fragments and the screenshots of the last run go. */
import { mkdirSync, rmSync } from 'node:fs';
import { FRAGMENT_DIR, SCREEN_DIR } from './lib/report';

export default function globalSetup(): void {
  rmSync(FRAGMENT_DIR, { recursive: true, force: true });
  rmSync(SCREEN_DIR, { recursive: true, force: true });
  mkdirSync(FRAGMENT_DIR, { recursive: true });
  mkdirSync(SCREEN_DIR, { recursive: true });
}
