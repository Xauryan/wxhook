import { expect, test } from '@playwright/test';
import fs from 'node:fs/promises';

test('real transcripts render one image per call; preview and download on desktop/mobile', async ({ page }) => {
  test.setTimeout(180000);
  const writes: string[] = [];
  page.on('request', r => { if (r.method() === 'POST') writes.push(r.url()); });
  await page.goto('/delivery-management');
  await page.getByRole('button', { name: '异常处理', exact: true }).click();
  await page.locator('tbody tr').filter({ hasText: '#10648' }).getByRole('button', { name: '处理 / 复盘' }).click();
  const section = page.locator('.delivery-dialogues');
  await expect(section.getByRole('heading', { name: '录音对话长图' })).toBeVisible({ timeout: 60000 });
  const output = '../.cache/dialogue-acceptance';
  await fs.mkdir(output, { recursive: true });
  for (const id of [20341895, 20349276]) {
    const call = section.locator('.delivery-dialogue-call').filter({ hasText: `录音 ${id}` });
    await call.getByRole('button', { name: '生成长图', exact: true }).click();
    await expect(call.getByText(/图片生成成功/)).toBeVisible({ timeout: 60000 });
    await call.getByRole('button', { name: '预览长图' }).click();
    const img = call.locator('img');
    await expect(img).toBeVisible();
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalHeight > 0)).toBe(true);
    const dimensions = await img.evaluate((el: HTMLImageElement) => ({ width: el.naturalWidth, height: el.naturalHeight }));
    const downloadPromise = page.waitForEvent('download');
    await call.getByRole('link', { name: '下载长图' }).click();
    const download = await downloadPromise;
    await download.saveAs(`${output}/${id}.png`);
    const png = await fs.readFile(`${output}/${id}.png`);
    expect(png.readUInt32BE(16)).toBe(dimensions.width);
    expect(png.readUInt32BE(20)).toBe(dimensions.height);
    const response = await page.request.get(await img.getAttribute('src') as string);
    expect(await response.body()).toEqual(png);
    await img.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/${id}-desktop.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await img.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${output}/${id}-mobile.png` });
    await img.evaluate(el => window.scrollTo(0, el.getBoundingClientRect().bottom + scrollY - innerHeight + 80));
    await page.screenshot({ path: `${output}/${id}-bottom.png` });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await call.getByRole('button', { name: '预览长图' }).click();
    console.log(`${id}: one complete PNG ${dimensions.width}x${dimensions.height}; download bytes match preview`);
  }
  expect(writes.every(url => url.endsWith('/dialogues/generate'))).toBe(true);
  expect(writes).toHaveLength(2);
  await expect(section.getByRole('button', { name: '发送所选长图到所选群' })).toBeDisabled();
});

test('group-message preview provides order context without sending or clearing existing receipts', async ({ page }) => {
  test.setTimeout(120000);
  const writes: { url: string; body: Record<string, unknown> }[] = [];
  page.on('request', r => { if (r.method() === 'POST') writes.push({ url: r.url(), body: r.postDataJSON() }); });
  await page.goto('/delivery-management');
  await page.getByRole('button', { name: '异常处理', exact: true }).click();
  await page.locator('tbody tr').filter({ hasText: '#10648' }).getByRole('button', { name: '处理 / 复盘' }).click();
  const section = page.locator('.delivery-dialogues');
  for (const id of [20341895, 20349276]) {
    await section.locator('.delivery-dialogue-call').filter({ hasText: `录音 ${id}` }).getByRole('checkbox').check();
  }
  await section.getByRole('checkbox', { name: '测试群', exact: true }).check();
  await expect(section.getByRole('button', { name: '发送所选长图到所选群' })).toBeDisabled();
  await expect(section.getByText('查找原消息', { exact: true })).toHaveCount(0);
  await section.getByRole('button', { name: '预览群消息 / 模拟检查' }).click();
  await expect(section.locator('.delivery-message-plan pre').first()).toContainText('【无法配送反馈】');
  await expect(section.locator('.delivery-message-plan pre').first()).toContainText('#10648');
  await expect(section.locator('.delivery-message-plan pre')).toHaveCount(1);
  await expect(section.locator('.delivery-message-plan')).toContainText('本次跳过');
  await section.locator('.delivery-message-plan').scrollIntoViewIfNeeded();
  await page.screenshot({ path: '../.cache/dialogue-acceptance/message-plan-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await section.locator('.delivery-message-plan').scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '../.cache/dialogue-acceptance/message-plan-mobile.png' });
  expect(writes).toHaveLength(1);
  expect(writes[0].body.simulate).toBe(true);
  expect(writes[0].url).toContain('/dialogues/send');
});
