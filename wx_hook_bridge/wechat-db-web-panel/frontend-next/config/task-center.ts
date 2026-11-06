import type { IncomingMessage, ServerResponse } from 'node:http'
import { HttpsProxyAgent } from 'https-proxy-agent'
import type { Plugin, ProxyOptions } from 'vite'

export const taskCenterTarget = process.env.TASK_CENTER_API_PROXY || 'https://jqr.47jsy.com'

export function taskCookie(header: string | undefined, target = taskCenterTarget) {
  const name = target.startsWith('https:') ? 'tc_remote_session' : 'tc_session'
  return (header || '').split(';').map((part) => part.trim())
    .find((part) => part.startsWith(name + '=')) || ''
}

export function taskOriginGuard(req: IncomingMessage, res: ServerResponse, next: () => void) {
  if (!req.url?.startsWith('/api/task-center/')) return next()
  if (!['GET', 'HEAD'].includes(req.method || '')) {
    const scheme = 'encrypted' in req.socket && req.socket.encrypted ? 'https' : 'http'
    if (req.headers.origin !== `${scheme}://${req.headers.host}`) {
      res.writeHead(403, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ code: 'csrf_failed', message: '请求来源无效。' }))
      return
    }
  }
  req.headers.cookie = taskCookie(req.headers.cookie)
  delete req.headers.authorization
  next()
}

const outboundProxy = process.env.HTTPS_PROXY || process.env.https_proxy
export const taskCenterProxy: ProxyOptions = {
  target: taskCenterTarget,
  changeOrigin: true,
  secure: true,
  // Respect the operator's existing HTTPS proxy; never change system networking.
  agent: taskCenterTarget.startsWith('https:') && outboundProxy
    ? new HttpsProxyAgent(outboundProxy, { keepAlive: true })
    : undefined,
}

export const taskCenterGuard: Plugin = {
  name: 'task-center-origin-guard',
  configureServer(server) { server.middlewares.use(taskOriginGuard) },
  configurePreviewServer(server) { server.middlewares.use(taskOriginGuard) },
}
