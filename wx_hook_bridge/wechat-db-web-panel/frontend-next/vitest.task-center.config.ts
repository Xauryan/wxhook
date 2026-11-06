import { defineConfig } from 'vitest/config'
import { playwright } from '@vitest/browser-playwright'

export default defineConfig({
  test: {
    include: ['src/task-center/**/*.test.ts'],
    browser: {
      enabled: true,
      headless: true,
      provider: playwright(),
      // Windows reserves 63305-63404 on this host; keep the isolated test listener explicit.
      api: { host: '127.0.0.1', port: 6195, strictPort: true },
      instances: [{ browser: 'chromium' }],
    },
  },
})
