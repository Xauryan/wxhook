import { expect, test } from "@playwright/test";

const routes = [
  ["/overview", "概览"],
  ["/contacts", "联系人"],
  ["/intention-notifications", "意向单通知"],
  ["/media", "媒体"],
  ["/sns", "朋友圈"],
  ["/contributions", "贡献"],
  ["/conversations", "会话"],
  ["/chat-list", "会话列表"],
  ["/search", "搜索"],
  ["/settings", "设置"],
  ["/mp", "公众号"],
] as const;

for (const [route, activeLabel] of routes) {
  test(`${route} renders the shared shell and active navigation`, async ({ page }) => {
    await page.goto(route);
    await expect(page.getByRole("button", { name: "返回概览" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
    await expect(page.getByRole("link", { name: activeLabel, exact: true })).toHaveClass(/active/);
    await expect(page.locator("main.main-content")).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}

test("global search submits a deep link", async ({ page }) => {
  await page.goto("/overview");
  const input = page.getByRole("textbox", { name: "搜索联系人、会话或内容" });
  await input.fill("本地 测试");
  await page.getByRole("button", { name: "开始搜索" }).click();
  await expect(page).toHaveURL(/\/search\?q=%E6%9C%AC%E5%9C%B0%20%E6%B5%8B%E8%AF%95$/);
});

test("realtime status and SSE connection are exposed in the shared shell", async ({ page }) => {
  const statusResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/realtime/status");
  const eventsResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/realtime/events");
  await page.goto("/overview");
  expect((await statusResponse).status()).toBe(200);
  expect((await eventsResponse).status()).toBe(200);
  const badge = page.locator("[data-realtime-state]");
  await expect(badge).toHaveAttribute("data-realtime-state", "connected", { timeout: 15_000 });
  await expect(badge).toContainText("已连接");
});

test("episode click loads and renders the complete message context", async ({ page }) => {
  test.setTimeout(60_000);
  const initialResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/conversations" && !url.searchParams.has("hour");
  });
  await page.goto("/conversations");
  const initialResponse = await initialResponsePromise;
  expect(initialResponse.status()).toBe(200);
  const firstEpisode = page.locator("button.episode-row").first();
  await expect(firstEpisode).toBeVisible();
  const responsePromise = page.waitForResponse((response) => /\/api\/conversations\/[^/?]+$/.test(response.url()) && response.request().method() === "GET");
  await firstEpisode.click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json() as { messages?: unknown[] };
  expect(payload.messages?.length ?? 0).toBeGreaterThan(0);
  const drawer = page.getByRole("dialog", { name: "完整会话上下文" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "完整消息上下文" })).toBeVisible();
  await expect(drawer.locator(".drawer-messages article").first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(firstEpisode).toBeFocused();
});

test("hour heat strip updates both the URL and API filter", async ({ page }) => {
  test.setTimeout(60_000);
  const initialResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/conversations" && !url.searchParams.has("hour");
  });
  await page.goto("/conversations");
  const initialResponse = await initialResponsePromise;
  expect(initialResponse.status()).toBe(200);
  const hourButton = page.getByRole("button", { name: /^筛选 0 时，/ });
  await expect(hourButton).toBeVisible();
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/conversations" && url.searchParams.get("hour") === "0";
  });
  await hourButton.click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  await expect(page).toHaveURL(/(?:\?|&)hour=0(?:&|$)/);
  await expect(hourButton).toHaveClass(/active/);
});

test("Chinese message search returns real FTS results", async ({ page }) => {
  const responsePromise = page.waitForResponse((response) => response.url().includes("/api/search?") && response.url().includes("q=%E9%A1%B9%E7%9B%AE"));
  await page.goto("/search?q=%E9%A1%B9%E7%9B%AE&scope=messages");
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json() as { fts_used?: boolean; items?: unknown[] };
  expect(payload.fts_used).toBe(true);
  expect(payload.items?.length ?? 0).toBeGreaterThan(0);
  await expect(page.locator(".search-results button").first()).toBeVisible();
});

test("space-separated pinyin contact search uses the FTS index", async ({ page }) => {
  const responsePromise = page.waitForResponse((response) => response.url().includes("/api/search?") && response.url().includes("scope=contacts"));
  await page.goto("/search?q=su%20shi%20qi&scope=contacts");
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json() as { fts_used?: boolean; items?: unknown[] };
  expect(payload.fts_used).toBe(true);
  expect(payload.items?.length ?? 0).toBeGreaterThan(0);
  await expect(page.locator(".search-results button").first()).toBeVisible();
});

test("chat list renders real sessions, messages, and detail tabs", async ({ page }) => {
  const listResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/chat-list");
  await page.goto("/chat-list");
  expect((await listResponse).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "会话列表" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await expect(page.locator(".window-controls, .chat-icon-rail")).toHaveCount(0);
  const firstSession = page.getByRole("listbox", { name: "真实会话列表" }).getByRole("option").first();
  await expect(firstSession).toBeVisible();
  await expect(firstSession).toHaveAttribute("aria-selected", "true");
  const latestMessage = page.locator(".chat-message-row-wrap").last();
  await expect(latestMessage).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1900);
  const messageScroll = page.locator(".chat-message-scroll");
  await expect.poll(() => messageScroll.evaluate((element) => Math.abs(element.scrollHeight - element.scrollTop - element.clientHeight) < 3)).toBe(true);
  await expect(latestMessage).toBeInViewport();
  await expect(page.getByRole("tab", { name: "资料" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "统计" }).click();
  await expect(page.getByRole("heading", { name: "会话统计" })).toBeVisible();

  const contactsResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/chat-list" && url.searchParams.get("scope") === "contacts";
  });
  await page.getByRole("tab", { name: /联系人/ }).click();
  expect((await contactsResponse).status()).toBe(200);
  await expect(page.getByRole("listbox", { name: "真实会话列表" }).getByRole("option").first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("chat list switches from the mobile session list to the message view", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/chat-list");
  const firstSession = page.getByRole("listbox", { name: "真实会话列表" }).getByRole("option").first();
  await expect(firstSession).toBeVisible();
  await firstSession.click();
  await expect(page.locator(".chat-list-workspace")).toHaveClass(/mobile-chat-open/);
  await expect(page.getByRole("button", { name: "返回会话列表" })).toBeVisible();
  await page.getByRole("button", { name: "返回会话列表" }).click();
  await expect(page.locator(".chat-list-workspace")).not.toHaveClass(/mobile-chat-open/);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("intention notification page keeps its WeChat preview usable on mobile", async ({ page }) => {
  await page.route(/\/api\/intention-notifications(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        config: {
          enabled: false,
          consumer_id: "yunsheng-wx-robot-01",
          token_configured: true,
          startup_policy: "new_only",
          activation_cutoff: null,
          target_groups: [],
        },
        runtime: {
          state: "disabled",
          transport: null,
          running: true,
          rest_base_url: "https://example.test/consumer",
          wss_url: "wss://example.test/consumer/ws",
        },
        today: {
          date: "2026-09-03",
          total: 0,
          sent: 0,
          skipped: 0,
          pending: 0,
          retrying: 0,
          team_snapshot_ready: false,
          team_snapshot_state: "no_events",
          on_duty_dialed_number_threshold: 10,
        },
        recent: [],
        schedule: {
          timezone: "Asia/Shanghai",
          slots: [
            { key: "stage_1200", label: "12:00", kind: "stage" },
            { key: "stage_1600", label: "16:00", kind: "stage" },
            { key: "stage_1830", label: "18:30", kind: "stage" },
            { key: "closing_2100", label: "21:00", kind: "closing" },
          ],
          reports: [],
        },
      }),
    });
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/intention-notifications");
  await expect(page.getByRole("heading", { name: "意向单通知" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "开启自动通知" })).not.toBeChecked();
  await page.getByRole("checkbox", { name: "开启自动通知" }).check();
  await expect(page.getByRole("radio", { name: /仅发送新单/ })).toBeChecked();
  await expect(page.getByRole("radio", { name: /补发积压单/ })).not.toBeChecked();
  await expect(page.locator(".wechat-message-row pre")).toContainText("【新单｜呼叫中心四组】");
  await expect(page.locator(".wechat-message-row pre")).toContainText("已破零 8/10 人");
  await page.getByRole("button", { name: "阶段战报" }).click();
  await expect(page.locator(".wechat-message-row pre")).toContainText("【阶段战报｜18:30】");
  await expect(page.locator(".wechat-message-row pre")).toContainText("待破零：杨帆、何佳");
  await page.getByRole("button", { name: "收官战报" }).click();
  await expect(page.locator(".wechat-message-row pre")).toContainText("【今日收官｜21:00】");
  await expect(page.locator(".wechat-message-row pre")).toContainText("明日优先关注：何佳");
  await expect(page.getByText("拨打号码 > 10", { exact: true })).toBeVisible();
  await expect(page.getByText("等待今日首单", { exact: true })).toBeVisible();
  await expect(page.getByText("北京时间 · 仅 4 个购机组", { exact: true })).toBeVisible();
  await expect(page.getByText("12:00", { exact: true })).toBeVisible();
  await expect(page.getByText("16:00", { exact: true })).toBeVisible();
  await expect(page.getByText("18:30", { exact: true })).toBeVisible();
  await expect(page.getByText("21:00", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("media content search uses real message FTS and opens the matching conversation", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/media");
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/search" && url.searchParams.get("q") === "项目" && url.searchParams.get("scope") === "messages";
  });
  await page.getByPlaceholder("搜索内容、关键词或文件名").fill("项目");
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payload = await response.json() as { fts_used?: boolean; items?: Array<{ id?: string; session_username?: string }> };
  expect(payload.fts_used).toBe(true);
  expect(payload.items?.length ?? 0).toBeGreaterThan(0);
  const firstResult = page.locator(".snippet-list button").first();
  await expect(firstResult).toBeVisible();
  await firstResult.click();
  await expect.poll(() => {
    const url = new URL(page.url());
    return {
      path: url.pathname,
      message: url.searchParams.get("message"),
      session: url.searchParams.get("session"),
      query: url.searchParams.get("q"),
    };
  }).toEqual({
    path: "/conversations",
    message: payload.items?.[0]?.id ?? null,
    session: payload.items?.[0]?.session_username ?? null,
    query: "项目",
  });
});

for (const width of [1024, 768, 375]) {
  test(`overview has no page-level horizontal overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/overview");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}
