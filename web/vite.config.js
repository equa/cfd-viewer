import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The viewer is served under /viz/ by the cfd-frontend nginx and at / when run
// standalone, so every asset URL must be RELATIVE -- an absolute /assets/... would
// hit the origin root (the cockpit SPA) instead of this service. `base: ''` is
// what makes the same build work in both places; the API calls use relative
// paths for the same reason (see src/api.js).
export default defineConfig({
  base: '',
  build: {
    outDir: 'dist',
    // three.js alone is ~700 kB minified; the warning is noise here, and the
    // client is served from the same container as the data it draws.
    chunkSizeWarningLimit: 2000,
  },
  server: {
    port: 5173,
    // `npm run dev` serves the UI with hot reload and forwards the scene
    // endpoints to the Python server (python main.py --data data --port 5003).
    proxy: {
      '/api': { target: 'http://localhost:5003', changeOrigin: true },
    },
  },
})
