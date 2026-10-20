import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: { outDir: 'dist', emptyOutDir: true },
  // `npm run dev` talks to a bridge running locally (live or --fake) for the API.
  server: { proxy: { '/api': 'http://127.0.0.1:8790' } },
});
