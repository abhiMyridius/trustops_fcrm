import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import clsx from 'clsx';
import { Icon } from '@/components/ui/Icon';

export function TypingIndicator() {
  return (
    <div className="flex items-center gap-1.5 px-4 py-3 bg-surface-secondary border border-border-hairline rounded-2xl rounded-bl-sm w-fit">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-text-faint"
          style={{ animation: 'dotPulse 1.1s ease-in-out infinite', animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  );
}

function StreamingText({ text, onDone }: { text: string; onDone?: () => void }) {
  const words = text.split(' ');
  const [count, setCount] = useState(words.length > 22 ? 0 : words.length);

  useEffect(() => {
    if (count >= words.length) {
      onDone?.();
      return;
    }
    const t = setTimeout(() => setCount((c) => c + 1), 18);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [count]);

  return <>{words.slice(0, count).join(' ')}</>;
}

export function AttachmentChip({ name, sizeKb, onClick }: { name: string; sizeKb: number; onClick?: () => void }) {
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      onClick={onClick}
      className={clsx(
        'flex items-center gap-2 bg-white border border-border-strong rounded-lg px-2.5 py-2 text-left max-w-[240px]',
        onClick && 'hover:border-carbon-moss hover:bg-surface-tertiary cursor-pointer transition-colors',
      )}
    >
      <span className="w-7 h-7 rounded-md bg-carbon-moss-deep flex items-center justify-center shrink-0">
        <Icon name="document" size={13} className="text-quantum-leaf" />
      </span>
      <span className="flex flex-col min-w-0 leading-tight">
        <span className="text-[12px] font-semibold text-text-primary truncate">{name}</span>
        <span className="text-[10.5px] text-text-faint">{sizeKb} KB</span>
      </span>
    </Tag>
  );
}

export function ChatBubble({
  role,
  text,
  animate = false,
  onDone,
  attachments,
  onAttachmentClick,
}: {
  role: 'ai' | 'po';
  text: string;
  animate?: boolean;
  onDone?: () => void;
  attachments?: { name: string; sizeKb: number }[];
  onAttachmentClick?: (name: string, sizeKb: number) => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22 }}
      className={clsx('flex flex-col gap-1.5', role === 'po' ? 'items-end' : 'items-start')}
    >
      {text && (
        <div
          className={clsx(
            'max-w-[86%] px-4 py-2.5 text-[13.5px] leading-relaxed rounded-2xl whitespace-pre-wrap',
            role === 'po'
              ? 'bg-carbon-moss text-white rounded-br-sm'
              : 'bg-surface-secondary text-text-primary border border-border-hairline rounded-bl-sm',
          )}
        >
          {animate ? <StreamingText text={text} onDone={onDone} /> : text}
        </div>
      )}
      {attachments && attachments.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {attachments.map((a) => (
            <AttachmentChip key={a.name} name={a.name} sizeKb={a.sizeKb} onClick={onAttachmentClick ? () => onAttachmentClick(a.name, a.sizeKb) : undefined} />
          ))}
        </div>
      )}
    </motion.div>
  );
}

export function ReplyChips({ chips, onSelect }: { chips: string[]; onSelect: (v: string) => void }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.15 }} className="flex flex-wrap gap-2 pl-1">
      {chips.map((c) => (
        <button
          key={c}
          onClick={() => onSelect(c)}
          className="text-[12.5px] font-medium text-carbon-moss bg-white border border-border-strong rounded-full px-3.5 py-1.5 hover:bg-surface-tertiary transition-colors cursor-pointer"
        >
          {c}
        </button>
      ))}
    </motion.div>
  );
}
