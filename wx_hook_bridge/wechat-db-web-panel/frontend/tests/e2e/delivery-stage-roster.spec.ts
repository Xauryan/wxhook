import { expect, test } from '@playwright/test';

test('current report excludes disabled staff and retains historical text without sending', async ({ page }) => {
  test.setTimeout(120000);
  const writes: string[] = [];
  page.on('request', request => { if (request.method() === 'POST') writes.push(request.url()); });
  await page.goto('/delivery-management');
  const table = page.getByRole('table').filter({ has: page.getByRole('columnheader', { name: '首拨', exact: true }) });
  await expect(table.locator('tbody tr')).toHaveCount(8, { timeout: 45000 });
  for (const name of ['吴家豪', '黄展元', '蔡子权', '禤家财']) await expect(table).not.toContainText(name);
  for (const name of ['吴志聪', '李文烨']) await expect(table).toContainText(name);
  await page.getByText('定时与提醒', { exact: true }).click();
  await expect(page.getByLabel('定时阶段报表（图片）', { exact: true })).toBeVisible();
  await page.getByText('阶段报表记录', { exact: true }).click();
  await page.getByRole('button', { name: '预览阶段报表' }).first().click();
  await expect(page.getByLabel('微信群消息内容')).toContainText('配送阶段报表');
  await expect(page.getByRole('img', { name: '配送阶段报表图片' })).toHaveCount(0);
  expect(writes).toHaveLength(0);
});
