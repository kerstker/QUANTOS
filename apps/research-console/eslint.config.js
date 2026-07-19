// ESLint 9 flat config (Fix 2 — Phase 0 frontend hardening).
// Real linting, separate from `npm run lint` (kept as `tsc --noEmit` for existing tooling/CI).
// Run with `npm run eslint`.
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactPlugin from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';

export default tseslint.config(
  {
    ignores: ['dist/**', 'node_modules/**', 'coverage/**'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactPlugin.configs.flat.recommended,
  reactPlugin.configs.flat['jsx-runtime'],
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      // Scoped intentionally to what Phase 0 asked for — NOT the full `recommended-latest` bundle,
      // which in react-hooks 7.x also pulls in experimental React-Compiler-oriented rules
      // (set-state-in-effect, immutability, ...) that fire on this codebase's normal
      // fetch-in-effect / local-accumulator patterns and are out of scope for this pass.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      // 14 pre-existing `any` usages tracked for a later cleanup phase — warn, don't fail, for now.
      '@typescript-eslint/no-explicit-any': 'warn',
    },
    settings: {
      react: { version: 'detect' },
    },
  },
  {
    // Vitest/node config files run outside the browser bundle.
    files: ['vite.config.ts', 'src/test/**/*.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
);
