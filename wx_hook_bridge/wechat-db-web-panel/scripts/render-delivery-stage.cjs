const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('../frontend/node_modules/@playwright/test');
const esc = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const number = value => value == null ? '暂缺' : String(value);

async function render(data, output) {
  const css = await fs.readFile(path.join(__dirname, 'delivery-stage-report.css'), 'utf8');
  const date = data.date.split('-');
  const dateLabel = date.length === 2 ? `${date[0]}月${date[1]}日` : data.date;
  const range = data.roster_policy === 'currently_active' ? '当前启用配送员' : data.roster_policy === 'historical_assignments' ? '历史配送统计范围' : '本次快照人员';
  const cells = (values, period) => values.map((value, index) => `<td class="${period ? `stage ${index === 0 ? 'stage-start' : ''} ${value === 0 ? 'zero' : 'active'}` : index === 0 ? 'dial' : ''}">${esc(number(value))}</td>`).join('');
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>${css}</style><article class="report">
  <div class="topline"><span>${esc(dateLabel)} · 配送运营</span><span class="preview">${esc(range)}</span></div>
  <header><h1>配送阶段报表</h1><div class="time">${esc(data.slot)}</div></header>
  <div class="period"><span>本阶段 <strong>${esc(data.period_label)}</strong></span><span>今日累计截至 <strong>${esc(data.cutoff)}</strong></span></div>
  <section class="kpis">
    <div class="kpi primary"><div class="label">今日拨号</div><div class="value ${data.daily_calls == null ? 'missing' : ''}"><span data-kpi="daily">${esc(number(data.daily_calls))}</span><small>次</small></div><div class="sub">本次统计人员合计</div></div>
    <div class="kpi"><div class="label">本阶段拨号</div><div class="value ${data.period_calls == null ? 'missing' : ''}"><span data-kpi="period">${esc(number(data.period_calls))}</span><small>次</small></div><div class="sub">${data.period_active_staff == null ? '本阶段人员数据暂缺' : `本阶段 ${data.period_active_staff} 人有拨号`}</div></div>
    <div class="kpi"><div class="label">今日有通话记录</div><div class="value ${data.active_staff == null ? 'missing' : ''}">${esc(number(data.active_staff))}<span class="denominator"> / ${esc(data.total_staff)}</span><small>人</small></div><div class="sub">${data.active_staff == null ? '部分人员数据暂缺' : `${data.total_staff-data.active_staff} 人暂无通话记录`}</div></div>
  </section>
  <div class="section-title"><h2>人员明细</h2><span>按今日拨号次数排序</span></div>
  <div class="table-wrap"><table><colgroup><col class="name"><col><col><col><col><col><col></colgroup>
    <thead><tr><th rowspan="2">配送员</th><th colspan="3">今日累计</th><th colspan="3" class="stage stage-start">本阶段</th></tr>
    <tr><th>拨号</th><th>覆盖</th><th>接通</th><th class="stage stage-start">拨号</th><th class="stage">覆盖</th><th class="stage">接通</th></tr></thead>
    <tbody>${data.rows.map(row => `<tr data-id="${row.staff_id}"><th>${esc(row.name)}</th>${cells(row.daily,false)}${cells(row.period,true)}</tr>`).join('')}</tbody></table>
    ${data.rows.length ? '' : '<div class="empty">本次统计人员暂无拨号记录</div>'}
  </div>
  ${data.zero_names.length ? `<section class="zero-panel"><div class="zero-head"><h2>今日暂无通话记录</h2><span>${data.zero_names.length} 人</span></div>
    <div class="names">${data.zero_names.map(name => `<span data-zero>${esc(name)}</span>`).join('')}</div><div class="zero-note">以上人员今日累计与本阶段各项数据均为 0。</div></section>` : ''}
  ${data.has_missing ? '<div class="missing-note">部分数据暂缺，未按 0 计算；汇总与人员范围需核对。</div>' : ''}
  <footer><b>统计口径</b>　拨号单位为“次”；覆盖、接通单位为去重电话号码。号码按配送员分别统计，跨人、跨阶段不累加。</footer>
  </article></html>`;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 980, height: 1800 }, deviceScaleFactor: 1 });
    await page.setContent(html); await page.evaluate(() => document.fonts.ready);
    const rows = await page.locator('tbody tr').evaluateAll(rows => rows.map(row => ({ id: Number(row.dataset.id), name: row.querySelector('th').textContent,
      values: [...row.querySelectorAll('td')].map(cell => cell.textContent) })));
    const expected = data.rows.map(row => ({ id: row.staff_id, name: row.name, values: [...row.daily,...row.period].map(number) }));
    if (JSON.stringify(rows) !== JSON.stringify(expected)) throw Error('Stage data rendering mismatch');
    if (JSON.stringify(await page.locator('[data-zero]').allTextContents()) !== JSON.stringify(data.zero_names)) throw Error('Zero-roster mismatch');
    for (const [key,value] of [['daily',data.daily_calls],['period',data.period_calls]]) {
      if (await page.locator(`[data-kpi="${key}"]`).textContent() !== number(value)) throw Error('KPI mismatch');
    }
    const box = await page.locator('.report').boundingBox();
    if (box.height > 100000) throw Error('Stage image too tall');
    if (await page.locator('.report').evaluate(root => [...root.querySelectorAll('*')].some(el => el.getBoundingClientRect().right > root.getBoundingClientRect().right + 1))) throw Error('Stage content overflow');
    await page.locator('.report').screenshot({ path: output });
    const png = await fs.readFile(output);
    if (png.readUInt32BE(20) < Math.floor(box.height)) throw Error('Stage footer clipped');
    return { width: png.readUInt32BE(16), height: png.readUInt32BE(20), rows: rows.length, total_staff: data.total_staff };
  } finally { await browser.close(); }
}
module.exports = { render };
if (require.main === module) fs.readFile(process.argv[2], 'utf8').then(body => render(JSON.parse(body.replace(/^\uFEFF/,'')), process.argv[3]))
  .then(result => console.log(JSON.stringify(result))).catch(error => { console.error(error.message); process.exitCode = 1; });
