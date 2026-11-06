const fs = require('node:fs/promises');
const { chromium } = require('../frontend/node_modules/@playwright/test');
const esc = v => String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const stamp = ms => ms == null ? '时间未知' : `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`;
async function render(input, output) {
  const speakers = new Map();
  const groups = [];
  for (const s of input.segments) {
    const key = s.speaker_id == null ? 'unknown' : String(s.speaker_id);
    if (!speakers.has(key)) speakers.set(key, key === 'unknown' ? '?' : String.fromCharCode(65 + [...speakers.keys()].filter(k => k !== 'unknown').length));
    const last = groups.at(-1);
    if (last && last.key === key) last.text += s.text;
    else groups.push({ key, label: speakers.get(key), text: s.text, start: s.begin_time });
  }
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
  *{box-sizing:border-box}body{margin:0;color:#182c22;background:#f7f9f7;font-family:"Microsoft YaHei",sans-serif}
  article{width:900px;padding:32px}header{border-bottom:1px solid #dce5df;padding-bottom:22px}h1{font-size:32px;margin:0 0 12px}.meta{font-size:22px;line-height:1.7}
  .row{display:flex;gap:12px;margin:22px 0;align-items:flex-start}.right{flex-direction:row-reverse}.badge{flex-shrink:0;background:#267c52;color:white;padding:8px;border-radius:8px;margin-top:30px;min-width:38px;text-align:center}
  .stack{max-width:740px;min-width:0}.time{font-size:18px;color:#637269;margin-bottom:5px}.right .time{text-align:right}.bubble{white-space:pre-wrap;overflow-wrap:anywhere;font-size:27px;line-height:1.65;padding:13px 18px;border:1px solid #d0e7d7;border-radius:4px 14px 14px;background:#e4f2e8}.right .bubble{background:white;border-color:#dbe3dd}
  footer{padding-top:20px;border-top:1px solid #dce5df;font-size:18px;color:#637269}
  </style><article><header><h1>${esc(input.title)} · ${esc(input.call_id)}</h1><div class="meta">意向单 #${esc(input.order_id)} · ${esc(input.customer)}</div><div class="meta">关联员工：${esc(input.employee)}<br>${esc(input.call_time)} · ${esc(input.duration)} 秒</div></header>
  ${groups.map(g => `<section class="row ${g.label === 'B' ? 'right' : ''}"><div class="badge">${g.label}</div><div class="stack"><div class="time">${stamp(g.start)}</div><div class="bubble">${esc(g.text)}</div></div></section>`).join('')}<footer>本通录音转写结束</footer></article></html>`;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 900, height: 1200 }, deviceScaleFactor: 1 });
    await page.setContent(html);
    await page.evaluate(() => document.fonts.ready);
    const actual = (await page.locator('.bubble').allTextContents()).join('');
    if (actual !== input.segments.map(s => s.text).join('')) throw Error('逐字校验失败');
    const box = await page.locator('article').boundingBox();
    // Explicit renderer resource ceiling, not a claimed WeChat transport limit.
    if (box.height > 100000) throw Error('长图超过本地渲染上限 100000px；未分页或截断');
    await page.locator('article').screenshot({ path: output, timeout: 60000 });
    const png = await fs.readFile(output);
    if (png.readUInt32BE(20) < Math.ceil(box.height)) throw Error('图片底部尺寸校验失败');
    return { width: png.readUInt32BE(16), height: png.readUInt32BE(20), characters: actual.length, segments: input.segments.length };
  } finally { await browser.close(); }
}
module.exports = { render };
if (require.main === module) {
  fs.readFile(process.argv[2], 'utf8').then(s => render(JSON.parse(s.replace(/^\uFEFF/, '')), process.argv[3])).then(r => console.log(JSON.stringify(r))).catch(e => { console.error(e.message); process.exitCode = 1; });
}
