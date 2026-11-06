import type { Count, Report } from './api'

const escape = (value: unknown) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ]!
  )

export function reportHtml(report: Report) {
  const titles: Record<string, string> = {
    annual: '年度与长期报告',
    dual: '双人报告',
    group: '群聊画像',
    footprint: '个人足迹',
  }
  const table = (rows: Count[]) =>
    `<table><thead><tr><th>项目</th><th>消息数</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escape(row.label)}</td><td>${row.count.toLocaleString('zh-CN')}</td></tr>`).join('')}</tbody></table>`
  const metrics = [
    ['消息总数', report.totals.message_count],
    ['我发送的', report.totals.sent_count],
    ['收到的', report.totals.received_count],
    ['活跃天数', report.totals.active_days],
    ['文本字符数', report.totals.text_characters],
  ]
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>${escape(titles[report.kind])}</title><style>body{max-width:1024px;margin:32px auto;padding:20px;font:15px/1.7 system-ui;color:#182333}h1{font-size:30px}h2{margin-top:32px;font-size:20px}.muted{color:#637084;font-size:12px;overflow-wrap:anywhere}.metrics{display:flex;flex-wrap:wrap;gap:12px}.metric{border:1px solid #dce1e7;border-radius:8px;flex:1;min-width:130px;padding:16px}.metric b{display:block;font-size:24px}table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid #e3e7eb;padding:10px;text-align:left;overflow-wrap:anywhere}th{background:#f4f6f9}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}li{overflow-wrap:anywhere}</style><main><p class="muted">微信本地分析 · ${escape(report.year || '全部年份')}</p><h1>${escape(titles[report.kind])}${report.session ? ' · ' + escape(report.session.display_name) : ''}</h1><p class="muted">账号 ${escape(report.account)} · 快照 ${escape(report.snapshot_time)} · 范围 ${escape(report.scope)}</p><div class="metrics">${metrics.map(([label, value]) => `<div class="metric">${escape(label)}<b>${escape(typeof value === 'number' ? value.toLocaleString('zh-CN') : value)}</b></div>`).join('')}</div><h2>留存与活跃</h2><p>${escape(report.totals.first_date)} — ${escape(report.totals.last_date)} · 最长连续活跃 ${report.longest_streak.days} 天</p><h2>月度趋势</h2>${table(report.monthly)}<h2>消息类型</h2>${table(report.categories)}<h2>${report.kind === 'group' ? '发言排行' : '会话排行'}</h2>${table((report.kind === 'group' ? report.speakers : report.ranking).map((row) => ({ label: row.name, count: row.count })))}<h2>统计说明</h2><ul>${report.notes.map((note) => `<li>${escape(note)}</li>`).join('')}</ul><details><summary>查看完整统计数据</summary><pre>${escape(JSON.stringify(report, null, 2))}</pre></details></main></html>`
}
