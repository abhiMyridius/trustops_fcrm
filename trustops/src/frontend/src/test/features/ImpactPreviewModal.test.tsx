import { screen } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import type { ConfigSection } from '@/api/types/config';
import { ImpactPreviewModal } from '@/features/analyst/config/ImpactPreviewModal';
import { server } from '@/mocks/server';
import { renderUi } from '../render';

const section = { section: 'field-sets', title: 'Field sets', draft: { etag: 'etag-1' } } as unknown as ConfigSection;

describe('the publish dialog', () => {
  it('says so when the impact preview could not be computed, instead of showing nothing', async () => {
    server.use(
      http.post(
        paths.policy.configPreview('field-sets'),
        () => HttpResponse.json({ type: 'about:blank', title: 'Upstream unavailable', status: 503, detail: 'Policy service is down.' }, { status: 503 }),
      ),
    );
    renderUi(<ImpactPreviewModal section={section} open onClose={() => undefined} />);
    expect(await screen.findByText(/impact preview is not available/i)).toBeInTheDocument();
    expect(screen.getByText(/policy service is down/i)).toBeInTheDocument();
  });
});
