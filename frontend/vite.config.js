import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs on :8000. In production the API serves the built files itself.
export default defineConfig({
  plugins: [react()],
  server: { port: 5190, proxy: { '/api': 'http://127.0.0.1:8000' } },
})
