/**
 * "Complete by hand" (flow F-path): a case whose agent step failed twice is in handoff, and the
 * analyst finishes the step. Each open gate shows what failed and why, what the agent was given
 * (the context pack as the model received it), and takes the proposals the agent should have
 * produced (a JSON array of its proposal objects) with a reason of 20+ characters.
 *
 * The output is checked on the server like an agent's (the same proposal contracts, Guard 4's
 * source-free checks), so a stray score key or a paraphrased shape comes back as a list of
 * problems, not a decision. Claims in a manual output are NOT matched to their sources: the form
 * says so. No score, band or rating is ever typed here: no proposal has such a field.
 */
import { useState } from 'react';
import { ApiError, userMessage } from '@/api/errors';
import { useCompleteHandoff, useHandoffGates } from '@/api/queries/analystQueries';
import type { HandoffGate } from '@/api/types/analyst';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';

/** The shortest reason the server accepts (`HANDOFF_REASON_MIN_CHARS`). */
export const HANDOFF_REASON_MIN = 20;

/** The array in `text`, or the sentence that says why it is not one. */
export function parseOutput(text: string): { ok: true; output: unknown[] } | { ok: false; message: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, message: 'The output is not valid JSON.' };
  }
  if (!Array.isArray(parsed)) return { ok: false, message: 'The output must be a JSON array of proposals, like [ {...}, {...} ].' };
  return { ok: true, output: parsed };
}

/** The problems a 422 lists (`errors`), one line each. */
function problemsOf(err: unknown): string[] {
  if (!(err instanceof ApiError)) return [];
  return (err.problem.errors ?? []).map((e) => e.msg);
}

function GateForm({ caseId, gate }: { caseId: string; gate: HandoffGate }) {
  const complete = useCompleteHandoff(caseId);
  const toast = useToast();
  const [output, setOutput] = useState('[\n  \n]');
  const [reason, setReason] = useState('');
  const [formError, setFormError] = useState<string | undefined>();
  const [problems, setProblems] = useState<string[]>([]);
  const reasonOk = reason.trim().length >= HANDOFF_REASON_MIN;

  function submit() {
    setProblems([]);
    const parsed = parseOutput(output);
    if (!parsed.ok) {
      setFormError(parsed.message);
      return;
    }
    setFormError(undefined);
    complete.mutate(
      { gate_id: gate.gate_id, output: parsed.output, reason: reason.trim() },
      {
        onSuccess: (done) => toast.success(`${gate.agent} step completed by hand`, `${done.items} proposals recorded; the run continues.`),
        onError: (err) => {
          setProblems(problemsOf(err));
          setFormError(userMessage(err));
        },
      },
    );
  }

  return (
    <Card title={`Complete the ${gate.agent} step by hand`} subtitle={gate.step ? `Stage when it failed: ${gate.step}` : undefined}>
      <div className="flex flex-col gap-4">
        <p className="text-[13px] text-text-secondary">
          The {gate.agent} agent failed after {gate.attempt} attempts: <span className="font-mono text-[12px]">{gate.reason}</span>
        </p>
        <details className="rounded-md border border-border bg-surface-tertiary p-3">
          <summary className="cursor-pointer text-[13px] font-semibold">What the agent was given</summary>
          {gate.raw_inputs ? (
            <>
              <pre aria-label="Raw inputs" className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-[12px] leading-relaxed">
                {gate.raw_inputs}
              </pre>
              <p className="mt-2 text-[12px] text-text-muted">
                {gate.raw_inputs_truncated ? 'Truncated for display. ' : ''}
                Digest {gate.raw_inputs_sha256?.slice(0, 12)}… is on the audit record.
              </p>
            </>
          ) : (
            <p className="mt-2 text-[13px] text-text-secondary">
              {gate.raw_inputs_error ? `Not available: ${gate.raw_inputs_error}.` : "The agent's inputs were not kept for this job."}
            </p>
          )}
        </details>
        <Textarea
          label="Output: the proposals the agent should have produced (JSON array)"
          value={output}
          onChange={(e) => setOutput(e.target.value)}
          rows={10}
          spellCheck={false}
          className="font-mono text-[12px]"
          error={formError}
          hint="Claims you enter here are not matched to sources. Each item still goes to review."
        />
        {problems.length > 0 && (
          <ul aria-label="Problems with the output" className="list-disc pl-5 text-[13px] text-danger">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        <Textarea
          label="Why you are completing this step"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          hint={`At least ${HANDOFF_REASON_MIN} characters.`}
        />
        <div>
          <Button variant="primary" onClick={submit} loading={complete.isPending} disabled={!reasonOk || output.trim().length === 0}>
            Complete by hand
          </Button>
        </div>
      </div>
    </Card>
  );
}

/** The open handoff gates of a case, each with its form. Rendered only for a case in handoff. */
export function HandoffControl({ caseId }: { caseId: string }) {
  const gates = useHandoffGates(caseId, true);
  if (gates.isPending) return <p className="text-[13px] text-text-muted">Loading the steps waiting for you…</p>;
  if (gates.isError) {
    return (
      <Banner tone="warning" title="The failed steps could not be loaded">
        {userMessage(gates.error)}
      </Banner>
    );
  }
  const open = gates.data.gates.filter((g) => g.status === 'open');
  if (open.length === 0) {
    return (
      <Banner tone="info" title="No step is waiting for you">
        The failed steps have been completed. The run continues.
      </Banner>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {open.map((gate) => (
        <GateForm key={gate.gate_id} caseId={caseId} gate={gate} />
      ))}
    </div>
  );
}
