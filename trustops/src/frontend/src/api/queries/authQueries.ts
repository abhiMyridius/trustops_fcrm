import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { ApiError } from '../errors';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type { DemoUserGroup } from '../types/common';

/**
 * Seeded users for the demo picker. Identity serves the list only where demo login is on (ENV local,
 * DEMO_USERS_ENABLED); anywhere else it answers 404 (or 403), which means "no picker": the data is
 * `null` and the login page shows the username and password form. No retry: a refusal is the answer.
 */
export function useDemoUsers(enabled: boolean) {
  return useQuery({
    queryKey: keys.demoUsers,
    queryFn: async (): Promise<DemoUserGroup[] | null> => {
      try {
        return await api<DemoUserGroup[]>(paths.auth.demoUsers);
      } catch (err) {
        if (err instanceof ApiError && (err.status === 404 || err.status === 403)) return null;
        throw err;
      }
    },
    enabled,
    retry: false,
    staleTime: Infinity,
  });
}
