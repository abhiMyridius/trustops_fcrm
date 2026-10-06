/**
 * "Complete by hand": a case whose agent step failed twice is in handoff and the analyst finishes
 * the step. The control lists the open gates with what the agent was given, takes the proposals as
 * JSON and a reason of 20+ characters, and posts them to `POST /cases/{id}/handoff/{gate}/complete`.
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import type { HandoffGate } from '@/api/types/analyst';
import { HandoffControl, parseOutput } from '@/features/analyst/HandoffControl';
import { server } from '@/mocks/server';
import { renderUi } from '../render';

const CASE = 'FCRM-2026-0151';
const GATE = 'gate_h1';
const REASON = 'The agent failed twice; I wrote the clause from the policy text.';
const PROPOSALS = '[{"clause_id": "FFIEC-TPPP-04", "quote": "A bank must evaluate the processor."}]';

function gate(over: Partial<HandoffGate> = {}): HandoffGate {
  return {
    gate_id: GATE,
    agent: 'research',
    job_id: 'job_r1',
    step: 'assess',
    reason: 'guard4_rejected: schema mismatch',
    attempt: 2,
    raw_inputs_ref: 'agent_raw_inputs/job_r1',
    raw_inputs: '## Case\nConsumer remittances US to Mexico',
    raw_inputs_sha256: 'ab'.repeat(32),
    raw_inputs_truncated: false,
    raw_inputs_error: null,
    opened_at: '2026-10-06T09:00:00Z',
    status: 'open',
    ...over,
  };
}

function serve(gates: HandoffGate[]): void {
  server.use(http.get(paths.cases.handoffGates(CASE), () => HttpResponse.json({ case_id: CASE, gates })));
}

/** `fireEvent`-free typing: a JSON textarea has braces that `userEvent.type` reads as key syntax. */
async function paste(label: RegExp, text: string): Promise<void> {
  const box = await screen.findByLabelText(label);
  await userEvent.clear(box);
  await userEvent.click(box);
  await userEvent.paste(text);
}

describe('parseOutput', () => {
  it('takes a JSON array', () => {
    expect(parseOutput('[{"a": 1}]')).toEqual({ ok: true, output: [{ a: 1 }] });
    expect(parseOutput('[]')).toEqual({ ok: true, output: [] });
  });

  it('says why when it is not valid JSON or not an array', () => {
    expect(parseOutput('{nope')).toEqual({ ok: false, message: 'The output is not valid JSON.' });
    const notArray = parseOutput('{"a": 1}');
    expect(notArray.ok).toBe(false);
    expect(notArray.ok ? '' : notArray.message).toMatch(/JSON array of proposals/);
  });
});

describe('the Complete by hand control', () => {
  it('shows what failed, why and what the agent was given', async () => {
    serve([gate()]);
    renderUi(<HandoffControl caseId={CASE} />);

    expect(await screen.findByText('Complete the research step by hand')).toBeInTheDocument();
    expect(screen.getByText('guard4_rejected: schema mismatch')).toBeInTheDocument();
    expect(screen.getByLabelText('Raw inputs')).toHaveTextContent('Consumer remittances US to Mexico');
    expect(screen.getByText(/Digest abababababab/)).toBeInTheDocument();
  });

  it('says the inputs are not available when none were kept', async () => {
    serve([gate({ raw_inputs: null, raw_inputs_error: 'the inputs were not kept' })]);
    renderUi(<HandoffControl caseId={CASE} />);

    expect(await screen.findByText(/Not available: the inputs were not kept/)).toBeInTheDocument();
  });

  it('keeps the button off until the reason has 20 characters', async () => {
    serve([gate()]);
    renderUi(<HandoffControl caseId={CASE} />);
    const button = await screen.findByRole('button', { name: 'Complete by hand' });
    expect(button).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/Why you are completing this step/), 'too short');
    expect(button).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/Why you are completing this step/), ' and now it is long enough');
    expect(button).toBeEnabled();
  });

  it('posts the proposals and the reason, then says the run continues', async () => {
    serve([gate()]);
    let posted: unknown;
    server.use(
      http.post(paths.cases.completeHandoff(CASE, GATE), async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json(
          { case_id: CASE, gate_id: GATE, agent: 'research', job_id: 'job_r1', items: 1, decided_by: 'u-riyer', replayed: false },
          { status: 202 },
        );
      }),
    );
    renderUi(<HandoffControl caseId={CASE} />);
    await paste(/Output: the proposals/, PROPOSALS);
    await userEvent.type(screen.getByLabelText(/Why you are completing this step/), REASON);

    await userEvent.click(screen.getByRole('button', { name: 'Complete by hand' }));

    await waitFor(() => expect(posted).toBeDefined());
    expect(posted).toEqual({
      output: [{ clause_id: 'FFIEC-TPPP-04', quote: 'A bank must evaluate the processor.' }],
      reason: REASON,
    });
    expect(await screen.findByText('research step completed by hand')).toBeInTheDocument();
  });

  it('refuses text that is not a JSON array without calling the server', async () => {
    serve([gate()]);
    let calls = 0;
    server.use(
      http.post(paths.cases.completeHandoff(CASE, GATE), () => {
        calls += 1;
        return new HttpResponse(null, { status: 202 });
      }),
    );
    renderUi(<HandoffControl caseId={CASE} />);
    await paste(/Output: the proposals/, '{"not": "an array"}');
    await userEvent.type(screen.getByLabelText(/Why you are completing this step/), REASON);

    await userEvent.click(screen.getByRole('button', { name: 'Complete by hand' }));

    expect(await screen.findByText(/must be a JSON array of proposals/)).toBeInTheDocument();
    expect(calls).toBe(0);
  });

  it('lists each problem the server finds in the output', async () => {
    serve([gate()]);
    server.use(
      http.post(paths.cases.completeHandoff(CASE, GATE), () =>
        HttpResponse.json(
          {
            type: 'https://trustops.local/errors/validation_error',
            title: 'Validation error',
            status: 422,
            detail: 'The output does not pass the checks for the research agent.',
            errors: [
              { loc: 'body.output', msg: 'outputs[0] matches none of ClauseProposal, PrecedentMatch' },
              { loc: 'body.output', msg: 'outputs[1]: quote is under 12 characters' },
            ],
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    renderUi(<HandoffControl caseId={CASE} />);
    await paste(/Output: the proposals/, '[{"score": 4.1}]');
    await userEvent.type(screen.getByLabelText(/Why you are completing this step/), REASON);

    await userEvent.click(screen.getByRole('button', { name: 'Complete by hand' }));

    const list = await screen.findByLabelText('Problems with the output');
    expect(list).toHaveTextContent('outputs[0] matches none of ClauseProposal');
    expect(list).toHaveTextContent('outputs[1]: quote is under 12 characters');
    expect(screen.getByText('The output does not pass the checks for the research agent.')).toBeInTheDocument();
  });

  it('offers a form for each open gate and none for a decided one', async () => {
    serve([gate(), gate({ gate_id: 'gate_h2', agent: 'assessment', job_id: 'job_a1' }), gate({ gate_id: 'gate_h3', status: 'decided' })]);
    renderUi(<HandoffControl caseId={CASE} />);

    expect(await screen.findByText('Complete the research step by hand')).toBeInTheDocument();
    expect(screen.getByText('Complete the assessment step by hand')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Complete by hand' })).toHaveLength(2);
  });

  it('says so when no step is waiting', async () => {
    serve([gate({ status: 'decided', raw_inputs: null })]);
    renderUi(<HandoffControl caseId={CASE} />);

    expect(await screen.findByText('No step is waiting for you')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Complete by hand' })).not.toBeInTheDocument();
  });

  it('says the failed steps could not be loaded when the read fails', async () => {
    server.use(http.get(paths.cases.handoffGates(CASE), () => HttpResponse.json({ type: 'x', title: 'Forbidden', status: 403 }, { status: 403 })));
    renderUi(<HandoffControl caseId={CASE} />);

    expect(await screen.findByText('The failed steps could not be loaded')).toBeInTheDocument();
    expect(screen.getByText('Not available for your role.')).toBeInTheDocument();
  });
});
