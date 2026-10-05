import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type { DemoUserGroup } from '../types/common';

/** Seeded users for the demo picker (VITE_DEMO_USERS=true only; identity serves it in ENV=local only). */
export function useDemoUsers(enabled: boolean) {
  return useQuery({
    queryKey: keys.demoUsers,
    queryFn: () => api<DemoUserGroup[]>(paths.auth.demoUsers),
    enabled,
    staleTime: Infinity,
  });
}
