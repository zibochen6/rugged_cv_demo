/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: '#0f172a',
        panel: '#1e293b',
        border: '#334155',
        text: '#e5e7eb',
        muted: '#94a3b8',
        accent: '#22c55e',
        warn: '#f59e0b',
        error: '#ef4444',
      },
    },
  },
  plugins: [],
}
