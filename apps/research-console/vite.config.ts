/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'path';
import coveragePolicy from '../../config/ci_coverage_policy.json';

const repoRoot = path.resolve(__dirname, '../..');

function collectFrontendFiles(dir: string, baseDir: string): string[] {
  const results: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === 'dist' || entry.name === '.git') continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...collectFrontendFiles(fullPath, baseDir));
    } else if (entry.isFile()) {
      results.push(path.relative(baseDir, fullPath).split(path.sep).join('/'));
    }
  }
  return results;
}

function frontendSourceIdentity(): { releaseCommit: string; sourceSha256: string } {
  try {
    const releaseCommit = execFileSync('git', ['rev-parse', 'HEAD'], {
      cwd: repoRoot,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
    if (!/^[0-9a-f]{40}$/.test(releaseCommit)) {
      throw new Error('frontend build requires a full lowercase Git commit');
    }
    const tracked = execFileSync(
      'git',
      ['ls-files', '-z', '--', 'apps/research-console'],
      { cwd: repoRoot, stdio: ['ignore', 'pipe', 'ignore'] },
    ).toString('utf8').split('\0').filter(Boolean).sort();
    if (tracked.length === 0) throw new Error('frontend tracked source set is empty');
    const rows = tracked.map(relative => [
      relative,
      createHash('sha256').update(readFileSync(path.join(repoRoot, relative))).digest('hex'),
    ]);
    const sourceSha256 = createHash('sha256').update(JSON.stringify(rows)).digest('hex');
    return { releaseCommit, sourceSha256 };
  } catch {
    const files = collectFrontendFiles(__dirname, repoRoot).sort();
    const rows = files.map(relative => [
      relative,
      createHash('sha256').update(readFileSync(path.join(repoRoot, relative))).digest('hex'),
    ]);
    const sourceSha256 = createHash('sha256').update(JSON.stringify(rows)).digest('hex');
    return { releaseCommit: '0'.repeat(40), sourceSha256 };
  }
}

export default defineConfig(({ mode }) => {
  const fileEnv = loadEnv(mode, __dirname, 'VITE_');
  const env = (name: string, fallback = '') => {
    const value = process.env[name] ?? fileEnv[name];
    return value == null || value.trim() === '' ? fallback : value;
  };
  const localGatewayDefault = mode === 'production' ? '__QUANTOS_SAME_ORIGIN__' : '';
  const apiUrl = env('VITE_API_URL', localGatewayDefault);
  const runnerUrl = env('VITE_RUNNER_URL', localGatewayDefault);
  const mockMode = env('VITE_MOCK_MODE', 'false');
  const sourceIdentity = frontendSourceIdentity();
  return {
  plugins: [
    react(),
    {
      name: 'quantos-frontend-build-contract',
      apply: 'build',
      generateBundle() {
        this.emitFile({
          type: 'asset',
          fileName: 'quantos-build-contract.json',
          source: `${JSON.stringify({
            schema: 'quantos.frontend_build.v1',
            release_commit: sourceIdentity.releaseCommit,
            frontend_source_sha256: sourceIdentity.sourceSha256,
            api_url: apiUrl,
            runner_url: runnerUrl,
            mock_mode: mockMode,
          }, null, 2)}\n`,
        });
      },
    },
  ],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: { host: '0.0.0.0', port: 3000, allowedHosts: true, open: false },
  build: {
    // Keep React/Router in a stable vendor chunk; route-level React.lazy splits the per-page chunks.
    rollupOptions: { output: { manualChunks: { vendor: ['react', 'react-dom'] } } },
  },
  define: {
    'import.meta.env.VITE_API_URL': JSON.stringify(apiUrl),
    // Real API is the safe product default.  Mock mode must be explicitly
    // requested for isolated demos/tests so a stale fixture cannot look live.
    'import.meta.env.VITE_MOCK_MODE': JSON.stringify(mockMode),
    'import.meta.env.VITE_RUNNER_URL': JSON.stringify(runnerUrl),
    'import.meta.env.VITE_CONSOLE_API_VERSION': JSON.stringify(env('VITE_CONSOLE_API_VERSION', '0.1.0')),
  },
  // Phase 8: real UI test config — the audit flagged its absence (jsdom + @testing-library were
  // installed but no `test` block existed, so component/render tests could not run).
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    coverage: {
      provider: 'v8',
      include: [coveragePolicy.frontend.scope],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/test/**',
        'src/**/*.d.ts',
      ],
      thresholds: {
        lines: coveragePolicy.frontend.lines,
        statements: coveragePolicy.frontend.statements,
        functions: coveragePolicy.frontend.functions,
        branches: coveragePolicy.frontend.branches,
      },
    },
  },
  };
});
