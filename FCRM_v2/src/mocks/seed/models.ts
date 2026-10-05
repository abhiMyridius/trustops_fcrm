import type { AgentConfig, AgentName, ModelInfo } from '@/types';

export const MODEL_REFERENCE: ModelInfo[] = [
  {
    id: 'sonnet-5',
    name: 'Sonnet 5',
    provider: 'Anthropic',
    relativeSpeed: 'Balanced',
    relativeCost: '$$',
    strengths: 'Strong reasoning-to-cost balance; reliable structured extraction.',
    contextWindow: '200K tokens',
  },
  {
    id: 'opus-5',
    name: 'Opus 5',
    provider: 'Anthropic',
    relativeSpeed: 'Thorough',
    relativeCost: '$$$',
    strengths: 'Highest accuracy on ambiguous or messy source documents.',
    contextWindow: '200K tokens',
  },
  {
    id: 'haiku-4-5',
    name: 'Haiku 4.5',
    provider: 'Anthropic',
    relativeSpeed: 'Fast',
    relativeCost: '$',
    strengths: 'Low-latency classification and short conversational turns.',
    contextWindow: '200K tokens',
  },
  {
    id: 'gpt-5-1',
    name: 'GPT-5.1',
    provider: 'OpenAI',
    relativeSpeed: 'Balanced',
    relativeCost: '$$',
    strengths: 'Broad general knowledge; good at free-form document parsing.',
    contextWindow: '128K tokens',
  },
];

export const AGENT_NAMES: AgentName[] = [
  'Screening',
  'Intake',
  'Extraction',
  'Classification',
  'Submission Validation',
];

export const DEFAULT_AGENT_CONFIGS: AgentConfig[] = [
  { agent: 'Screening', modelId: 'haiku-4-5', effort: 'Standard' },
  { agent: 'Intake', modelId: 'sonnet-5', effort: 'Standard' },
  { agent: 'Extraction', modelId: 'sonnet-5', effort: 'High' },
  { agent: 'Classification', modelId: 'haiku-4-5', effort: 'Standard' },
  { agent: 'Submission Validation', modelId: 'sonnet-5', effort: 'Standard' },
];

/** Release tag per model, independent of the agent using it - what actually produced a given
 * extraction or proposal, alongside the agent's own prompt version. */
export const MODEL_VERSIONS: Record<string, string> = {
  'sonnet-5': 'v2026.06',
  'opus-5': 'v2026.06',
  'haiku-4-5': 'v2026.05',
  'gpt-5-1': 'v2026.03',
};

/** Instruction-set version per agent - the same model under a different prompt produces
 * different extractions and proposals, so this is what actually makes an AI action
 * reconstructable. Stamped onto activity events, not displayed. */
export const AGENT_PROMPT_VERSIONS: Record<AgentName, string> = {
  Screening: 'screening-prompt v6',
  Intake: 'intake-prompt v14',
  Extraction: 'extraction-prompt v9',
  Classification: 'classification-prompt v11',
  'Submission Validation': 'validation-prompt v5',
};
