/// <reference types="vitest/config" />
import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import { playwright } from '@vitest/browser-playwright'
import { readFile } from 'node:fs/promises'
import { taskCenterGuard, taskCenterProxy } from './config/task-center'

// https://vite.dev/config/
export default defineConfig({
  optimizeDeps: {
    include: [
      'zod',
      'react-day-picker/locale',
      '@radix-ui/react-dropdown-menu',
      '@radix-ui/react-popover',
      '@radix-ui/react-switch',
      '@tanstack/react-query-devtools',
      '@tanstack/react-router-devtools',
      'react-dom/client',
      'react-top-loading-bar',
    ],
  },
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    proxy: {
      '/api/task-center': taskCenterProxy,
      '/api': process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8765',
    },
  },
  preview: {
    host: '127.0.0.1',
    proxy: {
      '/api/task-center': taskCenterProxy,
      '/api': process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8765',
    },
  },
  plugins: [
    taskCenterGuard,
    {
      name: 'isolated-chat-media-fixtures',
      configureServer(server) {
        if (server.config.mode !== 'test') return
        server.middlewares.use(
          '/api/rich-fixture',
          async (request, response, next) => {
            const name = request.url?.split('?')[0]?.slice(1)
            const fixtures: Record<string, [string, string]> = {
              thumbnail: ['public/images/wechat-emoticons/28.gif', 'image/gif'],
              original: ['public/images/wechat-emoticons/28.gif', 'image/gif'],
              voice: ['src/test-utils/fixtures/rich-voice.wav', 'audio/wav'],
              video: ['src/test-utils/fixtures/rich-video.mp4', 'video/mp4'],
            }
            if (name === 'original-broken') {
              response.statusCode = 404
              response.end()
              return
            }
            const fixture = fixtures[name || '']
            if (!fixture) return next()
            try {
              const payload = await readFile(
                path.resolve(__dirname, fixture[0])
              )
              response.setHeader('Content-Type', fixture[1])
              response.setHeader('Content-Length', payload.length)
              response.end(payload)
            } catch (error) {
              next(error)
            }
          }
        )
      },
    },
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    // Node's test runner owns tests/*.test.mjs; this suite renders browser components.
    include: ['src/**/*.test.{ts,tsx}'],
    silent: 'passed-only',
    unstubEnvs: true,
    browser: {
      enabled: true,
      provider: playwright(),
      instances: [{ browser: 'chromium' }],
    },
    coverage: {
      // include: ['src/**/*.{js,jsx,ts,tsx}'], // Uncomment to expand the report to all src/**/* so untested modules appear as 0% coverage.
      exclude: [
        'src/components/ui/**',
        'src/assets/**',
        'src/tanstack-table.d.ts',
        'src/routeTree.gen.ts',
        'src/test-utils/**',
        'src/routes/**',
      ],
    },
  },
})
