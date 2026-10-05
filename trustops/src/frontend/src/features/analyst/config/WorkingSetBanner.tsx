import { useState } from 'react';
import { useDiscardDraft } from '@/api/queries/configQueries';
import { userMessage } from '@/api/errors';
import type { ConfigSection } from '@/api/types/config';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import { ImpactPreviewModal } from './ImpactPreviewModal';

/** Sticky working set: the server-side draft for this section (two analysts see the same draft). */
export function WorkingSetBanner({ section }: { section: ConfigSection }) {
  const discard = useDiscardDraft(section.section);
  const toast = useToast();
  const [publishing, setPublishing] = useState(false);
  const n = section.draft.changes.length;
  if (n === 0) return null;
  return (
    <div role="status" className="sticky top-16 z-20 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warning bg-warning-bg px-4 py-3 shadow-sm">
      <div className="text-[13px] text-warning-text">
        <p className="font-semibold">
          {n} unsaved change{n === 1 ? '' : 's'} · {section.title}
        </p>
        <ul className="mt-0.5">
          {section.draft.changes.map((c) => (
            <li key={c.path}>
              {c.path}: {c.before} → {c.after}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="ghost"
          loading={discard.isPending}
          onClick={() => discard.mutate(section.draft.etag, { onSuccess: () => toast.success('Draft discarded'), onError: (e) => toast.error('Not discarded', userMessage(e)) })}
        >
          Discard
        </Button>
        <Button size="sm" variant="primary" onClick={() => setPublishing(true)}>
          Publish…
        </Button>
      </div>
      <ImpactPreviewModal section={section} open={publishing} onClose={() => setPublishing(false)} />
    </div>
  );
}
