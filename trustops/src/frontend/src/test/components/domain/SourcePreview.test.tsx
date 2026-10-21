/**
 * The source preview (ADR-76): a citation chip opens the cited page with its lines marked and the
 * pages either side; a clause chip opens the clause verbatim for the analyst and the committee; the
 * owner's frame has no clause branch and the owner's request for a clause is refused (ADR-32).
 */
import { configure, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setAccessToken } from '@/api/client';
import { paths } from '@/api/endpoints';
import { ClauseChip, ClausePreviewBody } from '@/components/domain/ClausePreview';
import { SourceChip } from '@/components/domain/SourceChip';
import { SourcePreviewProvider } from '@/components/domain/SourcePreview';
import { resetMockState } from '@/mocks/server';
import { violations } from '../../axe';
import { RESTRICTED_TEXT, USERS, call, tokenFor } from '../../fixtures';
import { renderApp, renderUi } from '../../render';

configure({ asyncUtilTimeout: 5000 });

const CASE = 'FCRM-2026-0151';
const BUSINESS_CASE = 'business_case_remittance_MX.pdf';
const CITED = { label: 'case p.1 l.9', document: BUSINESS_CASE, doc_id: 'doc_1', page: 1, line: 9 };
const CLAUSE = 'FFIEC-TPPP-04';

describe('SourcePreview · a document citation', () => {
  beforeEach(async () => {
    resetMockState('submitted');
    setAccessToken(await tokenFor(USERS.priya));
  });

  it('opens the cited page with the cited line marked and six lines of context, then walks the pages', async () => {
    renderUi(
      <SourcePreviewProvider caseId={CASE}>
        <SourceChip source={CITED} />
      </SourcePreviewProvider>,
    );
    const chip = screen.getByRole('button', { name: 'Open source case p.1 l.9' });
    await userEvent.click(chip);

    const dialog = await screen.findByRole('dialog', { name: 'Source · case p.1 l.9' });
    expect(await within(dialog).findByText(BUSINESS_CASE)).toBeInTheDocument();
    expect(within(dialog).getByText(/Page 1 of 14, line 9 cited · 6 lines of context/)).toBeInTheDocument();
    const list = within(dialog).getByRole('list', { name: `Page 1 of ${BUSINESS_CASE}` });
    const items = within(list).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent(/^3/);
    const cited = items.find((li) => li.getAttribute('aria-current') === 'true');
    expect(cited).toHaveTextContent('Q1 2027');
    expect(within(dialog).getByRole('button', { name: 'Previous page' })).toBeDisabled();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Show the whole page' }));
    await waitFor(() => expect(within(within(dialog).getByRole('list', { name: `Page 1 of ${BUSINESS_CASE}` })).getAllByRole('listitem')[0]).toHaveTextContent(/^1/));

    await userEvent.click(within(dialog).getByRole('button', { name: 'Next page' }));
    expect(await within(dialog).findByText(/Page 2 of 14/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/cited/)).not.toBeInTheDocument();
    expect(await violations(document.body)).toEqual([]);

    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(chip).toHaveFocus();
  });

  it('opens the PDF in a new tab at the page being read', async () => {
    const tab = { location: { href: '' }, close: vi.fn() };
    const open = vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window);
    const objectUrl = vi.fn(() => 'blob:mock-pdf');
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: objectUrl }));
    try {
      renderUi(
        <SourcePreviewProvider caseId={CASE}>
          <SourceChip source={CITED} />
        </SourcePreviewProvider>,
      );
      await userEvent.click(screen.getByRole('button', { name: 'Open source case p.1 l.9' }));
      const dialog = await screen.findByRole('dialog');
      await userEvent.click(await within(dialog).findByRole('button', { name: 'Open the PDF' }));
      expect(open).toHaveBeenCalledWith('', '_blank');
      await waitFor(() => expect(tab.location.href).toBe('blob:mock-pdf#page=1'));
    } finally {
      open.mockRestore();
      vi.unstubAllGlobals();
    }
  });

  it('is plain text for a source that names no document, and outside a provider', () => {
    renderUi(
      <SourcePreviewProvider caseId={CASE}>
        <SourceChip source={{ label: 'owner answer 3' }} />
      </SourcePreviewProvider>,
    );
    renderUi(<SourceChip source={CITED} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getAllByText(/owner answer 3|case p\.1 l\.9/)).toHaveLength(2);
  });

  it('says so when the document has no page to show', async () => {
    renderUi(
      <SourcePreviewProvider caseId={CASE}>
        <SourceChip source={{ ...CITED, doc_id: 'doc_missing' }} />
      </SourcePreviewProvider>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Open source case p.1 l.9' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/No such page or document/);
    expect(within(dialog).getByRole('button', { name: 'Open the PDF' })).toBeInTheDocument();
  });
});

describe('SourcePreview · a clause citation', () => {
  afterEach(() => setAccessToken(null));

  it('opens the clause verbatim with its id, title, source and reference for an analyst', async () => {
    resetMockState('submitted');
    setAccessToken(await tokenFor(USERS.rahul));
    renderUi(
      <SourcePreviewProvider caseId={CASE} clause={ClausePreviewBody}>
        <ClauseChip clauseId={CLAUSE} label="FFIEC 2024 ed. · p.312" />
      </SourcePreviewProvider>,
    );
    await userEvent.click(screen.getByRole('button', { name: `Open clause ${CLAUSE}` }));

    const dialog = await screen.findByRole('dialog', { name: 'Source · FFIEC 2024 ed. · p.312' });
    expect(await within(dialog).findByRole('heading', { name: 'FFIEC BSA/AML Manual · Third-Party Payment Processors' })).toBeInTheDocument();
    expect(within(dialog).getByText(new RegExp(`${CLAUSE} · FFIEC 2024 ed. · p.312`))).toBeInTheDocument();
    expect(within(dialog).getByText(/Source: FFIEC · p\.312/)).toBeInTheDocument();
    expect(within(dialog).getByText(/banks should assess the risks of the processor/)).toBeInTheDocument();
    expect(await violations(document.body)).toEqual([]);
  });

  it('is a button for a committee member too', async () => {
    resetMockState('released');
    setAccessToken(await tokenFor(USERS.ana));
    renderUi(
      <SourcePreviewProvider caseId={CASE} clause={ClausePreviewBody}>
        <ClauseChip clauseId={CLAUSE} label="FFIEC 2024 ed. · p.312" />
      </SourcePreviewProvider>,
    );
    await userEvent.click(screen.getByRole('button', { name: `Open clause ${CLAUSE}` }));
    expect(await screen.findByText(/banks should assess the risks/)).toBeInTheDocument();
  });

  it('never reaches the owner: no clause branch in a frame without one, and the route refuses the PO (fence)', async () => {
    resetMockState('submitted');
    renderUi(
      <SourcePreviewProvider caseId={CASE}>
        <ClauseChip clauseId={CLAUSE} label="FFIEC 2024 ed. · p.312" />
      </SourcePreviewProvider>,
    );
    expect(screen.queryByRole('button', { name: /Open clause/ })).not.toBeInTheDocument();
    expect(screen.getByText('FFIEC 2024 ed. · p.312')).toBeInTheDocument();

    const po = await tokenFor(USERS.priya);
    expect((await call(po, paths.policy.clause(CLAUSE))).status).toBe(403);
    const analyst = await tokenFor(USERS.rahul);
    expect((await call(analyst, paths.policy.clause(CLAUSE))).status).toBe(200);
  });
});

describe('SourcePreview · the owner’s Overview', () => {
  it('opens a value’s source from the chip and shows nothing restricted', async () => {
    resetMockState('submitted');
    renderApp(`/case/${CASE}/overview`, { as: USERS.priya });
    const chip = await screen.findByRole('button', { name: 'Open source case p.1 l.2' });
    await userEvent.click(chip);

    const dialog = await screen.findByRole('dialog', { name: 'Source · case p.1 l.2' });
    const cited = (await within(dialog).findAllByRole('listitem')).find((li) => li.getAttribute('aria-current') === 'true');
    expect(cited).toHaveTextContent('Priya Nair, Payments');
    // The drawer adds nothing the owner may not read (the page's own field labels are not this feature's).
    expect(dialog.textContent ?? '').not.toMatch(RESTRICTED_TEXT);
  });
});
