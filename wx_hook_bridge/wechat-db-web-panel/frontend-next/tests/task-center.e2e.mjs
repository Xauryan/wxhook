// Local regression test using three isolated browser contexts and normal account APIs.
import { chromium } from 'playwright'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const origin = 'http://127.0.0.1:5195'
const artifacts = new URL('../../task-center/artifacts/', import.meta.url)
await mkdir(artifacts, { recursive: true })
const browser = await chromium.launch({ headless: true })
const errors = []
const contexts = []
const password = 'Local-test-only-1234'
const suffix = Date.now().toString().slice(-8)
const report = { identities: 3, externalRequests: [], propagationMs: null }

async function pageFor() {
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 } })
  contexts.push(context)
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin !== origin) {
      report.externalRequests.push(route.request().url())
      return route.abort()
    }
    return route.continue()
  })
  const page = await context.newPage()
  page.on('pageerror', e => errors.push(e.message))
  return page
}
async function visible(locator) { await locator.waitFor({state:'visible',timeout:10000}) }
async function submitDialog(page, values) {
  const dialog = page.getByRole('dialog')
  for (const [label,value] of Object.entries(values)) await dialog.getByLabel(label,{exact:true}).fill(value)
  await dialog.getByRole('button',{name:'确认提交',exact:true}).click()
  try {
    await dialog.waitFor({state:'hidden',timeout:3000})
  } catch (error) {
    const retry = dialog.getByRole('button',{name:'保留填写并刷新版本',exact:true})
    if (!await retry.isVisible()) throw new Error(await dialog.innerText(),{cause:error})
    for (const [label,value] of Object.entries(values)) assert.equal(await dialog.getByLabel(label,{exact:true}).inputValue(),value)
    await retry.click()
    await dialog.getByRole('button',{name:'确认提交',exact:true}).click()
    await dialog.waitFor({state:'hidden',timeout:10000})
    report.conflictInputPreserved = true
  }
}
async function enterEmail(peer, email) {
  await peer.goto(origin+'/tasks')
  await peer.getByLabel('Git 邮箱',{exact:true}).fill(email)
  assert.equal(await peer.locator('input[type="password"]').count(),0)
  await peer.screenshot({path:fileURLToPath(new URL('git-email-entry.jpg',artifacts))})
  await peer.getByRole('button',{name:'进入任务管理',exact:true}).click()
  await visible(peer.getByRole('button',{name:'月度任务',exact:true}))
  assert.equal(await peer.getByRole('button',{name:'成员管理',exact:true}).count(),0)
}
try {
  const owner = await pageFor(), alice = await pageFor(), bob = await pageFor()
  const admin = await owner.context().request.post(origin+'/api/task-center/v1/auth/owner-session', {headers:{Origin:origin},data:{login:'owner',password}})
  assert.equal(admin.status(),200)
  await owner.goto(origin+'/tasks')
  await visible(owner.getByRole('button',{name:'月度任务',exact:true}))
  await visible(owner.getByRole('link',{name:'微信聊天',exact:true}))
  await enterEmail(alice,'alice'+suffix+'@example.test')
  await enterEmail(bob,'bob'+suffix+'@example.test')
  await owner.getByRole('button',{name:'成员管理',exact:true}).click()
  await owner.screenshot({path:fileURLToPath(new URL('members.jpg',artifacts))})
  await owner.getByRole('button',{name:'月度任务',exact:true}).click()
  await owner.getByRole('button',{name:'新建任务',exact:true}).click()
  const title = '简单任务闭环 '+suffix
  const dialog = owner.getByRole('dialog')
  await dialog.getByLabel('任务标题',{exact:true}).fill(title)
  await dialog.getByLabel('截止时间（北京时间）',{exact:true}).fill('2026-12-31T18:00')
  await dialog.getByLabel('任务目标',{exact:true}).fill('让协作者知道项目需要开发什么。')
  await dialog.getByLabel('验收标准',{exact:true}).fill('说明清楚并提供 PR 链接，由所有者手动确认。')
  await dialog.getByRole('button',{name:'保存草稿',exact:true}).click()
  await visible(owner.getByRole('heading',{name:title,exact:true}))
  const taskUrl = owner.url()
  await alice.reload()
  assert.equal(await alice.getByRole('button',{name:new RegExp(title)}).count(),0)
  await owner.getByRole('button',{name:'发布任务',exact:true}).click()
  await alice.getByRole('button',{name:new RegExp(title)}).click()
  await visible(alice.getByRole('button',{name:'认领负责人',exact:true}))
  await alice.getByRole('button',{name:'认领负责人',exact:true}).click()
  await visible(alice.getByRole('button',{name:'提交成果',exact:true}))
  await bob.goto(taskUrl)
  await bob.getByRole('button',{name:'加入协作',exact:true}).click()
  await visible(bob.getByRole('button',{name:'退出协作',exact:true}))
  assert.equal(await bob.getByRole('button',{name:'提交成果',exact:true}).count(),0)
  await visible(alice.getByText('bob'+suffix+'@example.test',{exact:true}))

  await alice.getByRole('button',{name:'提交成果',exact:true}).click()
  const start = performance.now()
  await submitDialog(alice, {'成果说明':'功能已经完成，请查看 PR。','PR 链接（每行一个）':'https://github.com/example/private/pull/123'})
  await visible(owner.getByRole('button',{name:'确认完成',exact:true}))
  report.propagationMs = Math.round(performance.now()-start)
  assert.equal(await alice.getByRole('button',{name:'确认完成',exact:true}).count(),0)
  await owner.getByRole('button',{name:'退回修改',exact:true}).click()
  await submitDialog(owner, {'操作原因':'补充测试说明。'})
  await visible(alice.getByRole('button',{name:'提交成果',exact:true}))
  await alice.getByRole('button',{name:'提交成果',exact:true}).click()
  await submitDialog(alice, {'成果说明':'已补充测试说明。','PR 链接（每行一个）':'https://github.com/example/private/pull/123'})
  await visible(owner.getByRole('button',{name:'确认完成',exact:true}))
  await owner.getByRole('button',{name:'确认完成',exact:true}).click()
  await submitDialog(owner, {'确认说明（可选）':'我已自行看过 PR，确认完成。'})
  await visible(bob.getByText('已确认完成',{exact:true}))
  await visible(alice.getByText('已确认完成',{exact:true}))
  await owner.screenshot({path:fileURLToPath(new URL('manual-completed.jpg',artifacts))})
  // The original application shell and router own navigation, not a separate task app.
  await owner.getByRole('link',{name:'任务管理',exact:true}).click()
  await visible(owner.getByRole('button',{name:new RegExp(title)}))
  await owner.getByRole('button',{name:new RegExp(title)}).click()
  await visible(owner.getByText('已确认完成',{exact:true}))
  await owner.goBack()
  await visible(owner.getByRole('button',{name:new RegExp(title)}))
  await owner.goForward()
  await visible(owner.getByText('已确认完成',{exact:true}))
  await owner.getByRole('link',{name:'帮助中心',exact:true}).click()
  await owner.waitForURL('**/help-center')
  await owner.getByRole('link',{name:'任务管理',exact:true}).click()
  await visible(owner.getByRole('button',{name:'新建任务',exact:true}))
  assert.equal(new URL(owner.url()).origin,origin)
  assert.equal(await owner.getByRole('link',{name:'微信聊天',exact:true}).count(),1)
  report.originalShellNavigation = 'passed'

  // Offline browser keeps known data and reconnects without recreating accounts.
  await bob.context().setOffline(true)
  await visible(bob.getByRole('heading',{name:title,exact:true}))
  await bob.context().setOffline(false)
  await bob.reload()
  await visible(bob.getByText('已确认完成',{exact:true}))
  await bob.setViewportSize({width:390,height:844})
  assert.equal(await bob.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),true)
  await bob.screenshot({path:fileURLToPath(new URL('manual-mobile.jpg',artifacts))})

  await owner.getByRole('button',{name:'成员管理',exact:true}).click()
  const row = owner.getByRole('row').filter({hasText:'alice'+suffix})
  await row.getByRole('button',{name:'停用成员',exact:true}).click()
  await owner.getByRole('dialog').getByLabel('操作原因',{exact:true}).fill('本地撤销验证')
  await owner.getByRole('dialog').getByRole('button',{name:'确认操作',exact:true}).click()
  await visible(alice.getByRole('button',{name:'进入任务管理',exact:true}))
  await row.getByRole('button',{name:'恢复使用',exact:true}).click()
  await owner.getByRole('dialog').getByLabel('操作原因',{exact:true}).fill('恢复测试成员')
  await owner.getByRole('dialog').getByRole('button',{name:'确认操作',exact:true}).click()
  await alice.getByLabel('Git 邮箱',{exact:true}).fill('alice'+suffix+'@example.test')
  await alice.getByRole('button',{name:'进入任务管理',exact:true}).click()
  await visible(alice.getByRole('button',{name:'月度任务',exact:true}))
  await alice.goto(taskUrl)
  try { await visible(alice.getByText('已确认完成',{exact:true})) }
  catch(error) {
    console.log(JSON.stringify({stage:'restored-account',url:alice.url(),taskUrl,text:await alice.locator('body').innerText(),errors}))
    throw error
  }
  assert.deepEqual(errors,[])
  assert.deepEqual(report.externalRequests,[])
  report.taskUrl = taskUrl
  report.passed = true
  await writeFile(new URL('manual-e2e.json',artifacts),JSON.stringify(report,null,2))
  console.log(JSON.stringify(report))
} finally {
  for (const context of contexts) await context.close()
  await browser.close()
}
