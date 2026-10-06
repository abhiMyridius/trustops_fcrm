/**
 * `reasonSchema` is the one reason rule shared by every ReasonDialog and vote rationale
 * (design C §1.5 rule 5): trimmed, at least 20 characters.
 */
import { z } from 'zod';

export const REASON_MIN = 20;

export const reasonSchema = z
  .string()
  .trim()
  .min(1, 'A reason is required.')
  .min(REASON_MIN, `Give at least ${REASON_MIN} characters so the record explains the decision.`);
