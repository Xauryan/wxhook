import { expect, test } from '@playwright/test';

test('DeepSeek handles new feedback without manual recording selection', async ({ page }) => {
  test.setTimeout(120000);
  const writes: string[] = [];
  page.on('request', request => { if (request.method() === 'POST') writes.push(request.url()); });
  await page.goto('/delivery-management');
  await page.getByRole('button', { name: '异常处理', exact: true }).click();
  await page.locator('tbody tr').filter({ hasText: '#10734' }).getByRole('button', { name: '处理 / 复盘' }).click();
  await expect(page.getByRole('heading', { name: '本次反馈的录音筛选' })).toBeVisible({ timeout: 30000 });
  await expect(page.getByText('DeepSeek 已完成判断，现有录音未发现本次成单或失败沟通。')).toBeVisible();
  await expect(page.getByText('以下选择仅用于可选纠正，不操作也会由系统自动处理。')).toBeVisible();
  await expect(page.getByLabel('录音 20554286 归属')).toHaveValue('');
  await expect(page.getByLabel('录音 20554479 归属')).toHaveValue('');
  const detail = await page.request.get('http://127.0.0.1:8765/api/delivery-management/cases/46');
  const data = await detail.json();
  expect(data.selected_calls).toBeNull();
  expect(data.material_scope.model).toBe('deepseek-v4-flash');
  expect(data.material_scope.status).toBe('no_relevant');
  for (const id of [20554286, 20554479]) {
    expect(data.candidates.items.find((call: { id: number }) => call.id === id).delivery_transcript.status).toBe('succeeded');
  }
  expect(writes).toHaveLength(0);
});
