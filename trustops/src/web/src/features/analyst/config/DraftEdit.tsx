import { useState } from 'react';
import { PenLine } from 'lucide-react';
import { useSaveDraft } from '@/api/queries/configQueries';
import { userMessage } from '@/api/errors';
import type { ConfigSectionKey } from '@/api/types/config';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';

/** Edit one configuration value into the server-side draft (If-Match etag; 409 on a concurrent edit). */
export function DraftEdit({ section, etag, path, label, current }: { section: ConfigSectionKey; etag: string; path: string; label: string; current: string }) {
  const save = useSaveDraft(section);
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(current);
  return (
    <>
      <Button
        size="sm"
        variant="ghost"
        icon={<PenLine aria-hidden size={12} />}
        onClick={() => {
          setValue(current);
          setOpen(true);
        }}
        aria-label={`Edit ${label}`}
      >
        Edit
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={`Edit ${label}`}
        description="Goes into the draft working set; nothing changes until you publish."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!value.trim() || value === current}
              loading={save.isPending}
              onClick={() =>
                save.mutate(
                  { etag, path, before: current, after: value.trim() },
                  {
                    onSuccess: () => {
                      toast.success('Added to the draft');
                      setOpen(false);
                    },
                    onError: (e) => toast.error('Not saved', userMessage(e)),
                  },
                )
              }
            >
              Save to draft
            </Button>
          </>
        }
      >
        <Input label={label} value={value} onChange={(e) => setValue(e.target.value)} data-autofocus />
      </Modal>
    </>
  );
}
