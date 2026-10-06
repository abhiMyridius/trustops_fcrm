import { useEffect, useState } from 'react';
import { usePreviewDraft, usePublishDraft } from '@/api/queries/configQueries';
import { userMessage } from '@/api/errors';
import type { ConfigSection } from '@/api/types/config';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { REASON_MIN } from '@/components/ui/Form';
import { Modal } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';

/** Publish: impact preview from the server, a required version note, then a new immutable snapshot. */
export function ImpactPreviewModal({ section, open, onClose }: { section: ConfigSection; open: boolean; onClose: () => void }) {
  const preview = usePreviewDraft(section.section);
  const publish = usePublishDraft(section.section);
  const toast = useToast();
  const [note, setNote] = useState('');
  useEffect(() => {
    if (open) {
      setNote('');
      preview.mutate();
    }
    // Preview once per opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const ok = note.trim().length >= REASON_MIN;
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Publish ${section.title}`}
      description="A new version is created; the current one stays in the history."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!ok}
            loading={publish.isPending}
            onClick={() =>
              publish.mutate(
                { etag: section.draft.etag, note },
                {
                  onSuccess: (s) => {
                    toast.success(`${s.title} ${s.active_version} published`);
                    onClose();
                  },
                  onError: (e) => toast.error('Not published', userMessage(e)),
                },
              )
            }
          >
            Publish new version
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {preview.data && (
          <Banner tone="info" title="Impact">
            {preview.data.summary}
          </Banner>
        )}
        {preview.isError && (
          <Banner tone="danger" title="The impact preview is not available">
            {userMessage(preview.error)} You can still publish, but you will not see what it changes first.
          </Banner>
        )}
        {section.section === 'ai-agents' && <Banner tone="warning">A model change must pass the agent&apos;s eval set before it is used on cases.</Banner>}
        <Textarea
          label="Version note (required)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          hint={`At least ${REASON_MIN} characters. ${note.trim().length} so far.`}
          data-autofocus
        />
      </div>
    </Modal>
  );
}
