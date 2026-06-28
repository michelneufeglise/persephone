/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        serif: ['Cormorant Garamond', 'Georgia', 'serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      colors: {
        persephone: {
          50:  '#f5f0ff',
          100: '#ede5ff',
          200: '#d9ccff',
          300: '#bba3f8',
          400: '#9b72f0',
          500: '#7c44e3',
          600: '#6b29cc',
          700: '#591ea8',
          800: '#4a1d89',
          900: '#3d1a70',
          950: '#260d4f',
        },
        pomegranate: {
          50:  '#fff1f2',
          100: '#ffe0e3',
          200: '#ffc5cc',
          300: '#ff9aa6',
          400: '#ff5f75',
          500: '#f82c4a',
          600: '#e51235',
          700: '#c10b2c',
          800: '#a00d2a',
          900: '#841129',
          950: '#490210',
        },
        gold: {
          300: '#fde68a',
          400: '#fbbf24',
          500: '#f59e0b',
          600: '#d97706',
        },
      },
      animation: {
        'pulse-slow': 'pulse 3s ease-in-out infinite',
        'breathe': 'breathe 4s ease-in-out infinite',
        'orbit': 'orbit 8s linear infinite',
        'shimmer': 'shimmer 2s linear infinite',
        'float': 'float 6s ease-in-out infinite',
        'glow-pulse': 'glow-pulse 2s ease-in-out infinite',
      },
      keyframes: {
        breathe: {
          '0%, 100%': { transform: 'scale(1)', opacity: '0.8' },
          '50%': { transform: 'scale(1.08)', opacity: '1' },
        },
        orbit: {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% center' },
          '100%': { backgroundPosition: '200% center' },
        },
        float: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%': { transform: 'translateY(-8px)' },
        },
        'glow-pulse': {
          '0%, 100%': { boxShadow: '0 0 20px var(--accent-glow)' },
          '50%': { boxShadow: '0 0 40px var(--accent-glow), 0 0 80px var(--accent-glow)' },
        },
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [],
}
