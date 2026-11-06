// A read-only health endpoint identifies the exact supervised Vite preview worker.
import { parseArgs } from 'node:util'
import { preview } from 'vite'

const { values } = parseArgs({
  options: { port: { type: 'string' }, 'api-port': { type: 'string' } },
})
const port = Number(values.port)
const apiPort = Number(values['api-port'])
if (![port, apiPort].every((value) => Number.isInteger(value) && value >= 1024 && value <= 65535)) {
  throw new Error('Invalid loopback service port')
}
if (!process.env.JQR_WEB_INSTANCE) throw new Error('Missing web supervisor instance')
process.env.VITE_API_PROXY = `http://127.0.0.1:${apiPort}`

const server = await preview({
  preview: { host: '127.0.0.1', port, strictPort: true },
  plugins: [{
    name: 'owned-web-runtime-health',
    configurePreviewServer(web) {
      web.middlewares.use('/__jqr_web_health', (request, response) => {
        response.setHeader('Cache-Control', 'no-store')
        response.setHeader('Content-Type', 'application/json; charset=utf-8')
        if (request.method !== 'GET') {
          response.statusCode = 405
          response.end('{}')
          return
        }
        response.end(JSON.stringify({
          ok: true, instance: process.env.JQR_WEB_INSTANCE, pid: process.pid,
        }))
      })
    },
  }],
})
console.log(`Supervised web ready: http://127.0.0.1:${port}/chats`)
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.once(signal, async () => { await server.httpServer.close(); process.exit(0) })
}
