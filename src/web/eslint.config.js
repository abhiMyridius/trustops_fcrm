// ESLint carries only what oxlint does not: the jsx-a11y recommended set, React hooks rules and the
// visibility import fences (design C §1.6: features/po/** can never import score-bearing components or
// analyst/committee DTOs, so a PO screen is structurally unable to render a score, band or rating).
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';

const PO_FORBIDDEN = [
  {
    group: [
      '@/components/domain/ScoreBadge',
      '@/components/domain/RiskMatrix',
      '@/components/domain/ClauseCard',
      '@/components/domain/ControlRow',
      '@/components/domain/FactorBlock',
      '@/components/domain/VoteCard',
      '@/components/domain/PackSection',
      '@/api/types/analyst',
      '@/api/types/committee',
      '@/api/types/harness',
      '@/api/queries/analystQueries',
      '@/api/queries/committeeQueries',
      '@/api/queries/configQueries',
      '@/api/queries/assuranceQueries',
      '@/api/queries/harnessQueries',
      '@/lib/bands',
    ],
    message: 'Rule 3 / D-62: the PO view never renders score, band, rating, clause or vote data.',
  },
];

const APP_FORBIDDEN = [
  {
    group: ['@/mocks/*', '@/mocks/**', '../mocks/*', '../../mocks/*'],
    message: 'App code never imports the mock backend; it talks HTTP through src/api only.',
  },
];

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'public', 'src/api/generated'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2023, globals: globals.browser },
    plugins: { 'jsx-a11y': jsxA11y, 'react-hooks': reactHooks },
    rules: {
      ...jsxA11y.flatConfigs.recommended.rules,
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/consistent-type-imports': 'error',
    },
  },
  {
    files: ['src/features/**/*.{ts,tsx}', 'src/components/**/*.{ts,tsx}', 'src/app/**/*.{ts,tsx}', 'src/api/**/*.{ts,tsx}', 'src/auth/**/*.{ts,tsx}'],
    rules: { 'no-restricted-imports': ['error', { patterns: APP_FORBIDDEN }] },
  },
  {
    files: ['src/features/po/**/*.{ts,tsx}'],
    rules: { 'no-restricted-imports': ['error', { patterns: [...APP_FORBIDDEN, ...PO_FORBIDDEN] }] },
  },
);
