/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend runs on :8000 in development; proxy /api so the browser
// talks to a single origin and no CORS configuration is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
    // Whole-app jsdom renders take 1-3 s each on a busy machine; the 5 s default made the full
    // suite flaky (different tests timing out on each run). Assertions are unchanged.
    testTimeout: 20_000,
    // Every page test renders the whole app in jsdom; one worker per core starved them of CPU.
    maxWorkers: '50%',
  },
})
