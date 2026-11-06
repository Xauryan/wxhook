import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { DeliveryPushSettings } from "./DeliveryPushSettings";

function renderSettings() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DeliveryPushSettings day="2026-09-20" /></QueryClientProvider>);
}

function backend() {
  let config = { enabled: false, target_groups: [] as { room_id: string; name: string }[], activated_at: null };
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      const body = JSON.parse(String(options.body)); posts.push({ url, body });
      if (url.endsWith("/config")) config = { ...config, ...body };
    }
    const data = url.endsWith("/groups") ? { items: [{ room_id: "delivery-group@chatroom", name: "配送工作群" }, { room_id: "other-group@chatroom", name: "其他群" }] } : { config, runtime: { running: true, last_poll_at: null, last_error: null }, recent: [] };
    return { ok: true, json: async () => data };
  });
  vi.stubGlobal("fetch", fetch);
  return posts;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('shows call image quote evidence separately from bridge acceptance', async () => {
  const fetch = vi.fn(async (url: string, _options?: RequestInit) => ({ ok: true, json: async () => url.endsWith('/groups') ? { items: [] } : {
    config: { enabled: true, target_groups: [], activated_at: null }, runtime: { running: true },
    recent: [{ event_key: 'delivery.call:fixture', room_id: 'fixture@chatroom', room_name: 'Fixture', status: 'sent', error: null }],
    call_quotes: [{ event_key: 'delivery.call:fixture', room_id: 'fixture@chatroom', status: 'local_verified', error: null }],
  } }));
  vi.stubGlobal('fetch', fetch);
  renderSettings();
  expect(await screen.findByText(/统计图片原生引用对应的文字通知/)).toBeInTheDocument();
  expect(screen.getByText('已查库确认统计图片引用对应文字')).toBeInTheDocument();
  expect(fetch.mock.calls.every(args => args.length === 1 || args[1] === undefined)).toBe(true);
});

it('explains the material wait and exposes held feedback without sending', async () => {
  const posts: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === 'POST') posts.push(url);
    const data = url.endsWith('/groups') ? { items: [] } : {
      config: { enabled: false, target_groups: [], activated_at: null }, runtime: { running: true }, recent: [],
      feedback_wait: { minutes: 10, items: [{ case_id: 68, order_id: 10731, deadline_at: '2026-09-21T14:48:31+08:00', status: 'no_materials', error: null }] },
    };
    return { ok: true, json: async () => data };
  }));
  renderSettings();
  expect(await screen.findByText(/反馈等待录音最多 10 分钟/)).toBeInTheDocument();
  expect(screen.getByText('等待录音的反馈（1）')).toBeInTheDocument();
  expect(screen.getByText('#10731')).toBeInTheDocument();
  expect(screen.getByText('暂无就绪资料，未发送')).toBeInTheDocument();
  expect(posts).toEqual([]);
});

it('previews new stage images without falling back to a text bubble or sending', async () => {
  const posts: string[] = [];
  const key = 'delivery.stage:2026-09-20:21:00';
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === 'POST') posts.push(url);
    const data = url.includes('/notifications/stages/') ? { report_key: key, cutoff: '2026-09-20T21:00:00+08:00', media_type: 'image', message: 'Frozen alternate text' }
      : url.endsWith('/groups') ? { items: [] } : { config: { enabled: false, target_groups: [], activated_at: null }, runtime: {}, recent: [],
        stages: [{ report_key: key, cutoff: '2026-09-20T21:00:00+08:00', status: 'ready', media_type: 'image' }] };
    return { ok: true, json: async () => data };
  }));
  renderSettings();
  await screen.findByText('阶段报表记录');
  fireEvent.click(screen.getByText('阶段报表记录'));
  fireEvent.click(screen.getByRole('button', { name: '预览阶段报表' }));
  const image = await screen.findByRole('img', { name: '配送阶段报表图片' });
  expect(image.getAttribute('src')).toContain(encodeURIComponent(key) + '/image');
  expect(screen.queryByLabelText('微信群消息内容')).not.toBeInTheDocument();
  expect(posts).toHaveLength(0);
});

it("previews the selected group and message type without sending", async () => {
  const posts = backend(); renderSettings();
  expect(await screen.findByText("微信群预览")).toBeInTheDocument();
  expect(screen.getByText("示例消息")).toBeInTheDocument();
  expect(screen.getByLabelText("微信群消息内容")).toHaveTextContent("示例配送员");
  fireEvent.click(await screen.findByRole("checkbox", { name: /配送工作群/ }));
  expect(screen.getByRole("combobox", { name: "预览微信群" })).toHaveValue("delivery-group@chatroom");
  fireEvent.click(screen.getByRole("button", { name: /^配送统计$/ }));
  expect(screen.getByLabelText("微信群消息内容")).toHaveTextContent("暂无配送统计");
  expect(posts).toHaveLength(0);
});

it("searches and saves independent groups without sending a message", async () => {
  const posts = backend(); renderSettings();
  fireEvent.change(await screen.findByRole("searchbox", { name: "搜索目标微信群" }), { target: { value: "配送工作" } });
  expect(screen.queryByRole("checkbox", { name: /其他群/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: /配送工作群/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "自动推送新增配送电话（文字＋统计图）" }));
  fireEvent.click(screen.getByRole("button", { name: "保存配送推送设置" }));
  expect(await screen.findByText("设置已保存")).toBeInTheDocument();
  expect(posts).toEqual([{ url: "/api/delivery-management/notifications/config", body: { enabled: true, target_groups: [{ room_id: "delivery-group@chatroom", name: "配送工作群" }], scheduled_enabled: false, reminder_enabled: false, result_notifications_enabled: false, failure_notifications_enabled: false, stage_times: ["12:00","16:00","18:30","21:00"], first_call_minutes:60 } }]);
});

it("previews call text before its image using GET only", async () => {
  const posts = backend();
  const original = globalThis.fetch;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes('/report-preview?')) {
      expect(options?.method).toBeUndefined();
      return { ok: true, json: async () => ({ message: '【配送通话】订单 #10438', image_data_url: 'data:image/png;base64,aGVsbG8=', validity_note: '规则待确认' }) };
    }
    return original(url, options);
  }));
  render(<QueryClientProvider client={new QueryClient()}><DeliveryPushSettings day="2026-09-20" call={{ id: 101, staff_name: '配送员', call_time: '2026-09-20T19:59:06+08:00', call_duration: 69, is_connected: true, customer_phone_masked: '***4447', order_id: 10438, recording_available: true }} /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: '预览电话图文（不发送）' }));
  const image = await screen.findByRole('img', { name: '配送通话月度统计图片' });
  const text = screen.getByLabelText('微信群消息内容');
  expect(text).toHaveTextContent('【配送通话】订单 #10438');
  expect(text.compareDocumentPosition(image) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(posts).toHaveLength(0);
});

it("requires saved recipients and explicit confirmation for manual statistics", async () => {
  const posts = backend(); vi.spyOn(window, "confirm").mockReturnValue(false); renderSettings();
  const send = await screen.findByRole("button", { name: "推送 2026-09-20 配送统计" });
  expect(send).toBeDisabled();
  fireEvent.click(await screen.findByRole("checkbox", { name: /配送工作群/ }));
  expect(send).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存配送推送设置" }));
  await waitFor(() => expect(send).not.toBeDisabled());
  fireEvent.click(send);
  expect(posts).toHaveLength(1);
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(send);
  await screen.findByText(/已加入发送队列/);
  expect(posts[1].url).toBe("/api/delivery-management/notifications/send-overview");
  expect(posts[1].body.day).toBe("2026-09-20");
  fireEvent.click(send);
  await waitFor(() => expect(posts).toHaveLength(3));
  expect(posts[2].body.request_id).toBe(posts[1].body.request_id);
});
