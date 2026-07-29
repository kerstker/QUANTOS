/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig(({ mode }) => {
  const fileEnv = loadEnv(mode, __dirname, 'VITE_');
  const env = (name: string, fallback = '') => process.env[name] ?? fileEnv[name] ?? fallback;
  return {
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: { port: 3100, open: '/research' },
  build: {
    // Keep React/Router in a stable vendor chunk; route-level React.lazy splits the per-page chunks.
    rollupOptions: { output: { manualChunks: { vendor: ['react', 'react-dom'] } } },
  },
  define: {
    'import.meta.env.VITE_API_URL': JSON.stringify(env('VITE_API_URL')),
    // Real API is the safe product default.  Mock mode must be explicitly
    // requested for isolated demos/tests so a stale fixture cannot look live.
    'import.meta.env.VITE_MOCK_MODE': JSON.stringify(env('VITE_MOCK_MODE', 'false')),
    'import.meta.env.VITE_RUNNER_URL': JSON.stringify(env('VITE_RUNNER_URL')),
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
  },
  };
});
