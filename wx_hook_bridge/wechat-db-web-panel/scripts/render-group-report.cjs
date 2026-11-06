const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('../frontend-next/node_modules/playwright');
const { skinRegistry } = require('./vendor/chatluna/skins');
const { setAvatars } = require('./vendor/chatluna/skins/types');

const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

function escapeValues(value) {
  if (typeof value === 'string') return escape(value);
  if (Array.isArray(value)) return value.map(escapeValues);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, escapeValues(item)]));
  return value;
}

async function render(input, directory) {
  const skin = skinRegistry.getSafe(input.skin);
  const root = path.join(__dirname, 'vendor/chatluna/resources', skin.id);
  const raw = input.report;
  setAvatars(Object.fromEntries(raw.userStats.filter(user => /^data:image\/(png|jpeg|gif|webp);base64,/.test(user.avatar || '')).map(user => [user.userId, user.avatar])));
  const data = escapeValues(raw);
  const hour = Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', hourCycle: 'h23' }).format(new Date(raw.end * 1000)));
  const theme = input.theme === 'auto'
    ? (hour >= 19 || hour < 6 ? 'dark' : 'light')
    : input.theme;
  const values = {
    ...data,
    modelAttribution: `基于 ${data.model || 'AI'} 模型的群聊总结分析工具`,
    userStats: skin.formatUserStats(data.userStats), topics: skin.formatTopics(data.topics),
    userTitles: skin.formatUserTitles(data.userTitles), goldenQuotes: skin.formatGoldenQuotes(data.goldenQuotes),
    activeHoursChart: skin.generateActiveHoursChart(data.activeHoursData), theme,
  };
  let html = await fs.readFile(path.join(root, 'template_group.html'), 'utf8');
  const css = await fs.readFile(path.join(root, 'style.css'), 'utf8');
  html = html.replace(/\$\{(.*?)\}/g, (_, key) => values[key] ?? '');
  html = html.replace('<body>', `<body class="${theme}-theme">`);
  html = html.replace('</head>', `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'"><style>${css}
    body{margin:0;font-family:'Microsoft YaHei','Segoe UI',sans-serif}.report-coverage{padding:16px;text-align:center;font-size:13px;color:#666;background:#f4f1f8}
    .topic-detail,.quote-content,.quote-reason,.title-reason{white-space:pre-wrap;overflow-wrap:anywhere}
    @media print{body{background:white}.container{width:100%;box-shadow:none}.topic-card,.quote-card,.title-card{break-inside:avoid}}
    </style></head>`);
  const coverage = `<div class="report-coverage">统计区间 ${data.timeRange} · AI 分析 ${raw.analyzedMessages}/${raw.totalMessages} 条 · ${raw.truncatedMessages || 0} 条长消息截取前 1500 字<br>群友称号与 MBTI 为本次互动的娱乐解读</div>`;
  html = html.replace(/(<\/(?:div|main|article)>\s*)<\/body>/, `${coverage}$1</body>`);
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 900 }, deviceScaleFactor: 1, colorScheme: theme });
    await page.route('**/*', route => route.abort());
    await page.setContent(html, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => document.fonts.ready);
    const size = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight }));
    if (size.width > 1400 || size.height > 60000) throw new Error('Report layout exceeds rendering limits');
    await fs.writeFile(path.join(directory, 'report.html'), html);
    await page.locator(skin.containerSelector).screenshot({ path: path.join(directory, 'report.png') });
    await page.pdf({ path: path.join(directory, 'report.pdf'), format: 'A4', printBackground: true });
    console.log(JSON.stringify(size));
  } finally {
    await browser.close();
  }
}

fs.readFile(process.argv[2], 'utf8').then(body => render(JSON.parse(body), process.argv[3]))
  .catch(error => { console.error(error.message); process.exitCode = 1; });
