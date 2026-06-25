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
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      borderRadius: {
        panel: '18px',
        tile: '14px',
      },
      boxShadow: {
        panel: 'var(--shadow-panel)',
        lift: 'var(--shadow-lift)',
        glow: 'var(--glow-accent)',
      },
      fontSize: {
        '2xs': ['10.5px', '14px'],
      },
      backgroundImage: {
        grad: 'var(--grad)',
        'grad-cyan': 'var(--grad-cyan)',
      },
    },
  },
  plugins: [],
};
