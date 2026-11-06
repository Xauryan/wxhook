const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('../frontend/node_modules/@playwright/test');
const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const fields = [['delivery_total','订单总数','total'], ['success','成功','success'], ['in_progress','配送中',''],
  ['failed','失败','failed'], ['other','其余订单',''], ['rate','成功率','rate']];
function value(data, scope, key) {
  const metrics = data[scope];
  if (!metrics) return '暂缺';
  if (key === 'rate' && metrics[key] == null) return metrics.conflicts || (scope === 'pair' && !metrics.complete) ? '待核' : '—';
  return String(metrics[key]) + (key === 'rate' ? '%' : '');
}
async function render(data, output) {
  const css = await fs.readFile(path.join(__dirname, 'delivery-call-report.css'), 'utf8');
  // Embed the same installed Chinese family for normal and bold weights; no web fonts.
  const fontRoot = process.env.YSH_REPORT_FONT_DIR || path.join(process.env.WINDIR || 'C:/Windows', 'Fonts');
  const regular = (await fs.readFile(path.join(fontRoot, 'msyh.ttc'))).toString('base64');
  const bold = (await fs.readFile(path.join(fontRoot, 'msyhbd.ttc'))).toString('base64');
  const notes = [];
  if (data.overall) {
    notes.push(`其余：待分配 ${data.overall.unassigned} 单` + (data.overall.other > data.overall.unassigned ? `，其他 ${data.overall.other-data.overall.unassigned} 单` : '') + ` · 非配送 ${data.overall.non_delivery} 单`);
    if (data.overall.non_delivery) notes.push(`意向单共 ${data.overall.all_orders} 单；非配送不计配送成功率。`);
    if (data.overall.conflicts) notes.push(`状态待核 ${data.overall.conflicts} 单，成功率暂不展示。`);
    if (data.pair?.attribution_unknown) notes.push(`归属待核 ${data.pair.attribution_unknown} 单；右列仅列已确认归属，成功率待核。`);
  } else notes.push(data.unavailable_reason || '统计暂缺');
  const outcome = data.connected ? `接通 <b>${data.duration == null ? '时长未记录' : esc(data.duration) + ' 秒'}</b>` : '<b>未接通</b>';
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
  @font-face{font-family:ReportSans;src:url(data:font/collection;base64,${regular});font-weight:400}
  @font-face{font-family:ReportSans;src:url(data:font/collection;base64,${bold});font-weight:700}${css}</style>
  <article class="report"><header><h1>配送通话</h1><span class="order">#${esc(data.order_id)}</span></header>
  <div class="customer"><b>${esc(data.staff_name)} → ${esc(data.customer_name)}</b><span class="tail">尾号 ${esc(data.phone_tail)}</span></div>
  <div class="agent">外呼：${esc(data.agent_name)} · ${esc(data.group_name)}</div>
  <div class="times"><div><span class="label">分配</span>${esc(data.assigned_at)}</div><div><span class="label">拨打</span>${esc(data.call_time)}</div></div>
  <div class="call-state"><span>${outcome}</span><span>录音${data.recording ? '有链接' : '暂无链接'}</span></div>
  <div class="section-head"><h2>${esc(data.agent_name)} · 月度概况</h2><span class="month">${esc(data.month)}</span></div>
  <table><colgroup><col><col><col></colgroup><thead><tr><th>配送订单</th><th>全部配送</th><th>归属${esc(data.staff_name)}</th></tr></thead><tbody>
  ${fields.map(([key,label,cls]) => `<tr class="${cls}"><th>${label}</th>${['overall','pair'].map(scope => `<td data-value="${scope}.${key}">${esc(value(data,scope,key))}</td>`).join('')}</tr>`).join('')}
  </tbody></table><div class="note">${notes.map(esc).join('<br>')}</div>
  <footer class="foot"><div>成功率＝成功 / 配送总数（含配送中及其他）</div><div class="stamp">截至 ${esc(data.snapshot_at)} · 北京时间</div></footer></article></html>`;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 720, height: 1600 }, deviceScaleFactor: 1 });
    await page.setContent(html); await page.evaluate(() => document.fonts.ready);
    if (await page.evaluate(() => [...document.fonts].some(font => font.status !== 'loaded'))) throw Error('Report font unavailable');
    for (const scope of ['overall','pair']) for (const [key] of fields) {
      if (await page.locator(`[data-value="${scope}.${key}"]`).textContent() !== value(data,scope,key)) throw Error('Report metric mismatch');
    }
    const box = await page.locator('.report').boundingBox();
    if (box.height > 10000 || await page.locator('.report').evaluate(root => [...root.querySelectorAll('*')].some(el => el.scrollWidth > el.clientWidth + 1 || el.getBoundingClientRect().right > root.getBoundingClientRect().right + 1))) throw Error('Report content overflow');
    await page.locator('.report').screenshot({ path: output });
    const png = await fs.readFile(output);
    if (png.readUInt32BE(20) < Math.floor(box.height)) throw Error('Report image clipped');
    return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
  } finally { await browser.close(); }
}
module.exports = { render };
if (require.main === module) fs.readFile(process.argv[2], 'utf8').then(body => render(JSON.parse(body.replace(/^\uFEFF/,'')), process.argv[3]))
  .then(result => console.log(JSON.stringify(result))).catch(error => { console.error(error.message); process.exitCode = 1; });
