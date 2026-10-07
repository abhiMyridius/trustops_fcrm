import type { Problem } from './types/common';

/** A non-2xx response, carrying the server's problem+json body (design C §1.9). */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;

  constructor(status: number, problem: Problem) {
    super(problem.detail ?? problem.title);
    this.name = 'ApiError';
    this.status = status;
    this.problem = problem;
  }

  get traceId(): string | undefined {
    return this.problem.trace_id;
  }
}

/** What the monitor and the drawer say when the case exists but no run has recorded anything for it. */
export const NO_RUN_MESSAGE = 'No run is recorded for this case yet. A run starts when the owner uploads documents; seeded cases have none.';

function problemEndsWith(err: unknown, status: number, code: string): boolean {
  return err instanceof ApiError && err.status === status && typeof err.problem.type === 'string' && err.problem.type.endsWith(`/${code}`);
}

/**
 * The 404 S1 answers when the case exists but no assessment run has recorded anything for it
 * (problem type `.../no_run_recorded`). That is information, not a fault: a missing or not-yours case
 * answers the plain `not_found`, which stays "This case is not in your view".
 */
export function isNoRunRecorded(err: unknown): boolean {
  return problemEndsWith(err, 404, 'no_run_recorded');
}

/** The 409 S1 answers when the step an action or a read depends on has not happened yet (`.../not_ready`). */
export function isNotReady(err: unknown): boolean {
  return problemEndsWith(err, 409, 'not_ready');
}

/**
 * What the login form says when a sign-in is refused. The 401 there is not an ended session (nobody was
 * signed in): it is identity's own "Unknown user or wrong password.", so that is what the form shows.
 * Everywhere else a 401 still means the session has ended (`userMessage`).
 */
export function signInMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 401) return err.problem.detail ?? 'Unknown user or wrong password.';
  return userMessage(err);
}

/** The message a person sees for an error. Same copy for "missing" and "not yours" (no hints). */
export function userMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return 'Your session has ended. Sign in again.';
    if (err.status === 403) return 'Not available for your role.';
    if (isNoRunRecorded(err)) return NO_RUN_MESSAGE;
    if (err.status === 404) return 'This case is not in your view.';
    if (err.status === 409) return err.problem.detail ?? 'Someone changed this first. Reload to see the latest.';
    if (err.status === 422) return err.problem.detail ?? 'Some details are missing or invalid.';
    return err.problem.detail ?? err.problem.title;
  }
  if (err instanceof Error) return err.message;
  return 'Something went wrong.';
}
