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

/** The message a person sees for an error. Same copy for "missing" and "not yours" (no hints). */
export function userMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return 'Your session has ended. Sign in again.';
    if (err.status === 403) return 'Not available for your role.';
    if (err.status === 404) return 'This case is not in your view.';
    if (err.status === 409) return err.problem.detail ?? 'Someone changed this first. Reload to see the latest.';
    if (err.status === 422) return err.problem.detail ?? 'Some details are missing or invalid.';
    return err.problem.detail ?? err.problem.title;
  }
  if (err instanceof Error) return err.message;
  return 'Something went wrong.';
}
