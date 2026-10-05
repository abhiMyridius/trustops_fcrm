import type { StageStep } from '@/api/types/common';
import { Stepper } from '@/components/ui/Stepper';

/**
 * Case lifecycle. The server sends the steps already collapsed to the role's view: the PO sees
 * Draft · Submitted · Under assessment · Decision · Closed; the analyst sees the full H1 sequence.
 */
export function StageTracker({ steps }: { steps: StageStep[] }) {
  return <Stepper steps={steps} label="Case stage" compact={steps.length > 6} />;
}
