import { render, screen } from '@testing-library/react';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { routes } from '@/app/router';
import { RouteError } from '@/components/layout/RouteError';

function Boom(): never {
  throw new Error('secret internal detail at /srv/app/node_modules/x.js:12');
}

describe('route errors', () => {
  it('the route tree names an error element on its root route', () => {
    expect(routes[0]?.errorElement).toBeDefined();
  });

  it('a screen that throws shows a recoverable page, never the message or the stack', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const router = createMemoryRouter([{ path: '/', element: <Boom />, errorElement: <RouteError /> }]);
    render(<RouterProvider router={router} />);
    expect(screen.getByRole('heading', { name: /something went wrong/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument();
    expect(screen.queryByText(/secret internal detail/)).not.toBeInTheDocument();
    expect(screen.queryByText(/node_modules/)).not.toBeInTheDocument();
  });
});
