/**
 * When intake found more than one value for a field ("3 candidates found"), the Edit dialog lists them with
 * their places in the documents, so the owner can switch between them without retyping (D-132).
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HttpResponse, http } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { paths } from '@/api/endpoints';
import { ToastProvider } from '@/components/ui/Toast';
import { ValueDialog, type EditableValue } from '@/features/po/ValueDialog';
import { server } from '@/mocks/server';

const CASE = 'FCRM-2026-0152';

const ROW: EditableValue = {
  field_id: 'F04',
  label: 'Customer segment',
  value: 'Registered charities, income below GBP 5 million',
  source: { label: 'CharityDeposit_BusinessCase_v2.docx p.1 l.26', document: 'CharityDeposit_BusinessCase_v2.docx', page: 1, line: 26 },
  alternatives: [
    { value: 'Registered charities', source: { label: 'ProductSpec.pdf p.2 l.4', document: 'ProductSpec.pdf', page: 2, line: 4 } },
    { value: 'Charities and community interest companies', source: { label: 'CharityDeposit_BusinessCase_v2.docx p.3 l.11', document: 'CharityDeposit_BusinessCase_v2.docx', page: 3, line: 11 } },
  ],
};

function open(row: EditableValue, onClose: () => void = () => undefined) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ValueDialog caseId={CASE} row={row} onClose={onClose} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('the correction dialog with more than one value found', () => {
  it('lists the extracted value and the others, each with its place, the extracted one selected', () => {
    open(ROW);
    const group = screen.getByRole('group', { name: /Found in your documents/ });
    const options = within(group).getAllByRole('radio');
    expect(options).toHaveLength(3);
    expect(options[0]).toBeChecked();
    expect(within(group).getByText('Registered charities')).toBeInTheDocument();
    expect(within(group).getByText('Charities and community interest companies')).toBeInTheDocument();
    expect(within(group).getByText('Chosen first by the assistant')).toBeInTheDocument();
  });

  it('switches to an alternative with one click and saves it', async () => {
    let body: unknown = null;
    server.use(
      http.post(paths.cases.intakeValue(CASE, 'F04'), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({});
      }),
    );
    open(ROW);
    const save = screen.getByRole('button', { name: 'Save correction' });
    expect(save).toBeDisabled(); // nothing changed yet

    await userEvent.click(screen.getAllByRole('radio')[1] as HTMLElement); // 'Registered charities', from ProductSpec.pdf
    expect(screen.getByRole('textbox', { name: 'Corrected value' })).toHaveValue('Registered charities');
    expect(save).toBeEnabled();
    await userEvent.click(save);

    await vi.waitFor(() => expect(body).toMatchObject({ value: 'Registered charities' }));
  });

  it('unselects the list once the owner types their own value', async () => {
    open(ROW);
    const box = screen.getByRole('textbox', { name: 'Corrected value' });
    await userEvent.clear(box);
    await userEvent.type(box, 'Registered charities in England');
    for (const radio of screen.getAllByRole('radio')) expect(radio).not.toBeChecked();
  });

  it('shows the plain extracted line when there is nothing else to pick', () => {
    open({ ...ROW, alternatives: [] });
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    expect(screen.getByText(/Extracted:/)).toBeInTheDocument();
  });
});
