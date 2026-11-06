import { expect, test } from '@playwright/test';

test('confirmed feedback materials stay visible and simulation preserves the original primary', async ({ page }) => {
  test.setTimeout(120000);
  const writes: { url: string; body: Record<string, unknown> }[] = [];
  page.on('request', request => {
    if (request.method() === 'POST') writes.push({ url: request.url(), body: request.postDataJSON() });
  });
  await page.goto('/delivery-management');
  await page.getByRole('button', { name: '异常处理', exact: true }).click();
  await page.locator('tbody tr').filter({ hasText: '#10683' }).getByRole('button', { name: '处理 / 复盘' }).click();
  const section = page.locator('.delivery-dialogues');
  await expect(section).toContainText('仅续发已筛选的外呼成单及本次失败沟通', { timeout: 30000 });
  await expect(page.getByRole('heading', { name: '本次反馈的录音筛选' })).toBeVisible();
  await expect(page.getByText('DeepSeek 先选择少量候选', { exact: false })).toBeVisible();
  await expect(page.getByLabel('录音 20505785 归属')).toHaveValue('');
  await expect(page.getByLabel('录音 20530032 归属')).toHaveValue('');
  for (const id of [20505785, 20530032]) {
    const call = section.locator('.delivery-dialogue-call').filter({ hasText: `录音 ${id}` });
    await expect(call).toContainText('图片生成成功');
    await expect(call).toContainText('桥接已接受，群内可见待核对');
    await call.getByRole('button', { name: '预览长图' }).click();
    const image = call.locator('img');
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.complete && element.naturalHeight > 0)).toBe(true);
    await page.setViewportSize({ width: 390, height: 844 });
    await image.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../.cache/delivery-materials-20260920/case38-${id}-mobile.png` });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await call.getByRole('checkbox').check();
  }
  await section.getByRole('checkbox', { name: '测试群', exact: true }).check();
  await section.getByRole('button', { name: '预览群消息 / 模拟检查' }).click();
  const preview = section.locator('.delivery-message-plan');
  await expect(preview.locator('pre')).toHaveCount(1, { timeout: 60000 });
  await expect(preview.locator('pre').first()).toContainText('反馈人：未记录');
  await expect(preview).toContainText('此版本图片已提交，本次跳过');
  expect(writes).toHaveLength(1);
  expect(writes[0].url).toContain('/cases/38/dialogues/send');
  expect(writes[0].body.simulate).toBe(true);
});
