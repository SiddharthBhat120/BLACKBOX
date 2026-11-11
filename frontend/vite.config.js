import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: the API runs on :8001. Production: FastAPI serves the built files.
export default defineConfig({
  plugins: [react()],
  server: { port: 5174, proxy: { '/api': 'http://127.0.0.1:8001' } },
  build: { outDir: 'dist', chunkSizeWarningLimit: 1500 },
})
