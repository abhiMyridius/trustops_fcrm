import { useState } from 'react';
import { motion } from 'framer-motion';
import type { IntakeQuestion } from '@/mocks/scripts/intakeScript';
import { Icon } from '@/components/ui/Icon';
import { Input } from '@/components/ui/Form';
import { Button } from '@/components/ui/Button';

// Each question renders with its own inline answer box directly beneath it. The global chat
// input at the bottom of the page answers the same way - IntakeChat routes free text here too,
// so neither route is the "real" one.
function QuestionBlock({
  question,
  answer,
  onAnswer,
}: {
  question: IntakeQuestion;
  answer?: string;
  onAnswer: (fieldId: string, value: string) => void;
}) {
  const [draft, setDraft] = useState('');
  const answered = answer != null;

  function submit() {
    if (!draft.trim()) return;
    onAnswer(question.fieldId, draft.trim());
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="bg-surface-secondary border border-border-hairline rounded-2xl rounded-bl-sm px-4 py-2.5 text-[13.5px] leading-relaxed text-text-primary max-w-[92%]">
        {question.questionText}
      </div>
      {answered ? (
        <div className="flex items-start gap-2 pl-1 text-[13px] text-teal font-medium">
          <Icon name="check" size={14} className="shrink-0 mt-0.5" />
          <span className="text-text-primary">{answer}</span>
        </div>
      ) : (
        <div className="flex gap-2 pl-1">
          <Input
            autoFocus
            placeholder={question.placeholder}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            className="flex-1"
          />
          <Button size="sm" variant="primary" onClick={submit}>
            Send
          </Button>
        </div>
      )}
    </div>
  );
}

export function IntakeQuestionCard({
  questions,
  answers,
  onAnswer,
}: {
  questions: IntakeQuestion[];
  answers: Record<string, string>;
  onAnswer: (fieldId: string, value: string) => void;
}) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col gap-3.5 max-w-[92%]">
      {questions.map((q) => (
        <QuestionBlock key={q.id} question={q} answer={answers[q.fieldId]} onAnswer={onAnswer} />
      ))}
    </motion.div>
  );
}
