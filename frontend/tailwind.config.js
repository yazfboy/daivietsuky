/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        display: ['"Playfair Display"', 'Georgia', ' Cambria', '"Times New Roman"', 'serif'],
      },
      colors: {
        surface: {
          base: '#0B0C0E',
          elevation1: '#13151A',
          elevation2: '#1A1D24',
          elevation3: '#222630',
        },
        gold: {
          soft: '#D4B982',
          muted: '#9E8656',
        },
      },
    },
  },
  plugins: [],
};
