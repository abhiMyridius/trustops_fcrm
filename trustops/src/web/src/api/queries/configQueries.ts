/** Configuration sections (S3 config, S2 scoring and routing). Drafts are server-side (design C §1.5 rule 4). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../client';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type { ConfigSection, ConfigSectionKey, ImpactPreview } from '../types/config';

export function useConfigSection(section: ConfigSectionKey) {
  return useQuery({
    queryKey: keys.config(section),
    queryFn: () => api<ConfigSection>(paths.policy.config(section)),
    staleTime: 5 * 60 * 1000,
  });
}

export function useSaveDraft(section: ConfigSectionKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { etag: string; path: string; before: string; after: string }) =>
      api<ConfigSection>(paths.policy.configDraft(section), {
        method: 'PUT',
        headers: { 'If-Match': v.etag },
        body: { path: v.path, before: v.before, after: v.after },
      }),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.config(section) }),
  });
}

export function useDiscardDraft(section: ConfigSectionKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (etag: string) =>
      api<ConfigSection>(paths.policy.configDraft(section), { method: 'DELETE', headers: { 'If-Match': etag } }),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.config(section) }),
  });
}

export function usePreviewDraft(section: ConfigSectionKey) {
  return useMutation({ mutationFn: () => api<ImpactPreview>(paths.policy.configPreview(section), { method: 'POST', body: {} }) });
}

export function usePublishDraft(section: ConfigSectionKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { etag: string; note: string }) =>
      api<ConfigSection>(paths.policy.configPublish(section), { method: 'POST', headers: { 'If-Match': v.etag }, body: { note: v.note } }),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.config(section) }),
  });
}
