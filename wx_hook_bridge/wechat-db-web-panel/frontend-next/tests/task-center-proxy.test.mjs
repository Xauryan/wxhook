import assert from 'node:assert/strict'
import test from 'node:test'
import { taskCookie, taskOriginGuard } from '../config/task-center.ts'

test('only the cloud task cookie is forwarded, never local preview or WeChat credentials', () => {
  assert.equal(taskCookie('wechat=private; tc_session=old; tc_remote_session=cloud', 'https://jqr.47jsy.com'), 'tc_remote_session=cloud')
  assert.equal(taskCookie('tc_session=old; wechat=private', 'https://jqr.47jsy.com'), '')
  assert.equal(taskCookie('tc_session=fixture; tc_remote_session=cloud', 'http://127.0.0.1:8795'), 'tc_session=fixture')
})

test('local writes must come from the actual local origin', () => {
  for (const origin of ['https://evil.test', 'http://localhost:5195', undefined, 'http://127.0.0.1:5195']) {
    let allowed = false
    let status
    taskOriginGuard({url:'/api/task-center/v1/auth/identify', method:'POST', socket:{},
      headers:{host:'127.0.0.1:5195', origin}}, {writeHead(code) {status=code}, end() {}}, () => {allowed=true})
    assert.equal(allowed, origin === 'http://127.0.0.1:5195')
    if (!allowed) assert.equal(status, 403)
  }
})

test('GET/SSE and unrelated WeChat requests retain their existing routing', () => {
  for (const [url, method] of [['/api/task-center/v1/events','GET'], ['/api/chat/send','POST']]) {
    let allowed = false
    taskOriginGuard({url, method, headers:{}, socket:{}}, {}, () => {allowed=true})
    assert.equal(allowed, true)
  }
})
