import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { DeliveryManagementPage } from "./DeliveryManagementPage";

function renderPage() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DeliveryManagementPage /></QueryClientProvider>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("shows server errors without presenting a fabricated zero snapshot", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: "对接令牌未配置" }) }));
  renderPage();
  expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("对接令牌未配置");
  expect(screen.queryByText("配送员拨号次数")).not.toBeInTheDocument();
});

it("keeps failures separate from dated call statistics and labels unimplemented automation", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) => ({ ok: true, json: async () => url.endsWith("/notifications") ? { config: { enabled: false, target_groups: [], activated_at: null }, runtime: { running: true, last_poll_at: null, last_error: null }, recent: [] } : { items: [], has_more: false, next_before_id: null, snapshot_at: "2026-09-20T09:00:00+08:00" } })));
  renderPage();
  expect(await screen.findByText("每日跟进统计")).toBeInTheDocument();
  expect(screen.getByText("微信群预览")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "无法配送 / 未成交" }));
  expect(await screen.findByText("当前异常订单（不限统计日期）")).toBeInTheDocument();
});
