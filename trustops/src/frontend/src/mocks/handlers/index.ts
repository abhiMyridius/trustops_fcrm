import { auditHandlers } from './audit';
import { caseHandlers } from './case';
import { decisionHandlers } from './decision';
import { identityHandlers } from './identity';
import { policyHandlers } from './policy';
import { scoringHandlers } from './scoring';

export const handlers = [...identityHandlers, ...caseHandlers, ...scoringHandlers, ...policyHandlers, ...auditHandlers, ...decisionHandlers];
