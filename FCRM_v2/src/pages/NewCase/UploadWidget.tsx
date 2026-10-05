import { useState } from 'react';
import { motion } from 'framer-motion';
import { Icon } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';
import type { FileRef } from '@/types';

export function UploadWidget({ files, onUpload }: { files: FileRef[]; onUpload: () => void }) {
  const [picked, setPicked] = useState(false);
  const [uploading, setUploading] = useState(false);

  function handlePick() {
    setPicked(true);
  }

  function handleConfirm() {
    setUploading(true);
    setTimeout(() => {
      onUpload();
    }, 500);
  }

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="bg-white border border-border-strong rounded-lg p-4 flex flex-col gap-3 max-w-[86%]">
      {!picked ? (
        <button
          onClick={handlePick}
          className="flex items-center justify-center gap-2 border-2 border-dashed border-border-strong rounded-md py-6 text-[13px] font-semibold text-text-secondary hover:border-carbon-moss hover:text-carbon-moss cursor-pointer transition-colors"
        >
          <Icon name="upload" size={17} />
          Choose files to upload
        </button>
      ) : (
        <>
          <div className="flex flex-col gap-2">
            {files.map((f) => (
              <div key={f.name} className="flex items-center gap-2.5 text-[12.5px] text-text-primary bg-surface-tertiary rounded-md px-3 py-2">
                <Icon name="document" size={15} className="text-text-muted shrink-0" />
                <span className="flex-1 truncate font-medium">{f.name}</span>
                <span className="text-text-faint text-[11.5px] shrink-0">{f.sizeKb} KB</span>
              </div>
            ))}
          </div>
          <Button size="sm" variant="primary" loading={uploading} onClick={handleConfirm} className="self-start">
            {uploading ? 'Uploading…' : `Upload ${files.length} file${files.length === 1 ? '' : 's'}`}
          </Button>
        </>
      )}
    </motion.div>
  );
}
