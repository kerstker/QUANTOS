/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        panel: 'var(--panel)',
        'panel-2': 'var(--panel-2)',
        elevated: 'var(--elevated)',
        inset: 'var(--inset)',
        line: 'var(--line)',
        'line-strong': 'var(--line-strong)',
        ink: 'var(--ink)',
        muted: 'var(--muted)',
        faint: 'var(--faint)',
        accent: 'var(--accent)',
        'accent-2': 'var(--accent-2)',
        'accent-dim': 'var(--accent-dim)',
        'accent-ink': 'var(--accent-ink)',
        info: 'var(--info)',
        pos: 'var(--pos)',
        warn: 'var(--warn)',
        neg: 'var(--neg)',
        special: 'var(--special)',
        cyan: 'var(--cyan)',
        grid: 'var(--grid)',
        /* institutional amber scale (workbench signature) */
        amber: {
          300: '#f8d12e',
          400: '#f0b90b',
          500: '#d9a213',
          600: '#b8860b',
          900: '#4d3d08',
        },
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      borderRadius: {
        panel: '8px',
        tile: '6px',
      },
      boxShadow: {
        panel: 'var(--shadow-panel)',
        lift: 'var(--shadow-lift)',
        glow: 'var(--glow-accent)',
      },
      fontSize: {
        /* 2026-07-24 readability refactor: whole scale shifted up ~1.5px —
           3xs is now floor-legible (11px), body text lands at 15px. */
        '3xs': ['11px', '14px'],
        '2xs': ['12px', '16px'],
        xs: ['13px', '18px'],
        sm: ['14px', '20px'],
        base: ['15px', '22px'],
      },
      backgroundImage: {
        grad: 'var(--grad)',
        'grad-cyan': 'var(--grad-cyan)',
      },
    },
  },
  plugins: [],
};
