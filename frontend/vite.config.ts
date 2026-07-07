import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// The backend serves its session as a same-origin cookie and its CSP is
// connect-src 'self', so the SPA must look same-origin as the API. Proxy /api to
// the local backend in dev; production serves both from one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
